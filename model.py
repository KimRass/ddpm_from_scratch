# References:
    # https://github.com/KimRass/Transformer/blob/main/model.py
    # https://nn.labml.ai/diffusion/ddpm/unet.html
    # https://nn.labml.ai/diffusion/ddpm/index.html
    # https://github.com/davidADSP/Generative_Deep_Learning_2nd_Edition/blob/main/notebooks/08_diffusion/01_ddm/ddm.ipynb
    # https://huggingface.co/blog/annotated-diffusion

import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
import imageio
import math
from tqdm import tqdm
from pathlib import Path
import contextlib

from utils import image_to_grid, save_image


class Swish(nn.Module):
    def forward(self, x):
        return x * torch.sigmoid(x)


class TimeEmbedding(nn.Module):
    # "Parameters are shared across time, which is specified to the network using the Transformer
    # sinusoidal position embedding."
    def __init__(self, n_diffusion_steps, time_channels):
        super().__init__()

        self.d_model = time_channels // 4

        pos = torch.arange(n_diffusion_steps).unsqueeze(1)
        i = torch.arange(self.d_model // 2).unsqueeze(0)
        angle = pos / (10_000 ** (2 * i / self.d_model))

        self.pe_mat = torch.zeros(size=(n_diffusion_steps, self.d_model))
        self.pe_mat[:, 0:: 2] = torch.sin(angle)
        self.pe_mat[:, 1:: 2] = torch.cos(angle)

        # self.register_buffer("pos_enc_mat", self.pe_mat)

        self.layers = nn.Sequential(
            nn.Linear(self.d_model, time_channels),
            Swish(),
            nn.Linear(time_channels, time_channels),
        )

    def forward(self, diffusion_step):
        x = torch.index_select(
            self.pe_mat.to(diffusion_step.device), dim=0, index=diffusion_step,
        )
        return self.layers(x)


class ResConvSelfAttnBlock(nn.Module):
    def __init__(self, channels, n_groups=32):
        super().__init__()

        self.gn = nn.GroupNorm(num_groups=n_groups, num_channels=channels)
        self.qkv_proj = nn.Conv2d(channels, channels * 3, 1, 1, 0)
        self.out_proj = nn.Conv2d(channels, channels, 1, 1, 0)
        self.scale = channels ** (-0.5)

    def forward(self, x):
        b, c, h, w = x.shape
        skip = x

        x = self.gn(x)
        x = self.qkv_proj(x)
        q, k, v = torch.chunk(x, chunks=3, dim=1)
        attn_score = torch.einsum(
            "bci,bcj->bij", q.view((b, c, -1)), k.view((b, c, -1)),
        ) * self.scale
        attn_weight = F.softmax(attn_score, dim=2)        
        x = torch.einsum("bij,bcj->bci", attn_weight, v.view((b, c, -1)))
        x = x.view(b, c, h, w)
        x = self.out_proj(x)
        return x + skip


class ResBlock(nn.Module):
    def __init__(self, in_channels, out_channels, time_channels, attn=False, n_groups=32, drop_prob=0.1):
        super().__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.attn = attn

        self.layers1 = nn.Sequential(
            # "We replaced weight normalization with group normalization
            # to make the implementation simpler."
            nn.GroupNorm(num_groups=n_groups, num_channels=in_channels),
            Swish(),
            nn.Conv2d(in_channels, out_channels, 3, 1, 1),
        )
        self.time_proj = nn.Sequential(
            Swish(),
            nn.Linear(time_channels, out_channels),
        )
        self.layers2 = nn.Sequential(
            nn.GroupNorm(num_groups=n_groups, num_channels=out_channels),
            Swish(),
            nn.Dropout(drop_prob),
            nn.Conv2d(out_channels, out_channels, 3, 1, 1),
        )

        if in_channels != out_channels:
            self.conv = nn.Conv2d(in_channels, out_channels, 1, 1, 0)
        else:
            self.conv = nn.Identity()

        if attn:
            self.attn_block = ResConvSelfAttnBlock(out_channels)
        else:
            self.attn_block = nn.Identity()

    def forward(self, x, t):
        skip = x
        x = self.layers1(x)
        # "Diffusion time $t$ is specified by adding the Transformer sinusoidal position embedding
        # into each residual block."
        # "We condition all layers on $t$ by adding in the Transformer sinusoidal position embedding."
        x = x + self.time_proj(t)[:, :, None, None]
        x = self.layers2(x)
        x = x + self.conv(skip)
        return self.attn_block(x)


class Downsample(nn.Conv2d):
    def __init__(self, channels):
        super().__init__(channels, channels, 3, 2, 1)


class Upsample(nn.Module):
    def __init__(self, channels):
        super().__init__()

        self.layers = nn.Sequential(
            nn.Upsample(scale_factor=2, mode="nearest"),
            nn.Conv2d(channels, channels, 3, 1, 1),
        )

    def forward(self, x):
        return self.layers(x)


class UNet(nn.Module):
    def __init__(
        self,
        # "Our 32 × 32 models use four feature map resolutions (32 × 32 to 4 × 4),
        # and our 256 × 256 models use six."
        # 4 8 16 32: 4
        # 8 16 32 64: 4
        # 8 16 32 64 128 256: 6
        init_channels=32,
        channels=(64, 128, 256, 512),
        # "All models have self-attention blocks at the 16 × 16 resolution
        # between the convolutional blocks."
        # "We use self-attention at the 16 × 16 feature map resolution."
        attns=(False, False, True, False),
        # "All models have two convolutional residual blocks per resolution level."
        n_blocks=2,
        n_groups=32,
        n_diffusion_steps=1000,
    ):
        super().__init__()

        assert len(attns) == len(channels)

        self.init_conv = nn.Conv2d(3, init_channels, 3, 1, 1)

        self.time_channels = init_channels * 4
        self.time_embed = TimeEmbedding(
            n_diffusion_steps=n_diffusion_steps, time_channels=self.time_channels,
        )

        channels = (init_channels, *channels)
        self.down_block = nn.ModuleList()
        for idx in range(len(channels) - 1):
            in_channels = channels[idx]
            out_channels = channels[idx + 1]
            attn=attns[idx]
            for _ in range(n_blocks):
                # print("Res", in_channels, out_channels)
                self.down_block.append(
                    ResBlock(
                        in_channels=in_channels,
                        out_channels=out_channels,
                        time_channels=self.time_channels,
                        attn=attn,
                        n_groups=n_groups,
                    )
                )
                in_channels = out_channels

            if idx < len(channels) - 2:
                # print("Down", out_channels)
                self.down_block.append(Downsample(out_channels))

        self.mid_block = nn.ModuleList(
            [
                ResBlock(
                    in_channels=out_channels,
                    out_channels=out_channels,
                    time_channels=self.time_channels,
                    attn=True,
                    n_groups=n_groups,
                ),
                ResBlock(
                    in_channels=out_channels,
                    out_channels=out_channels,
                    time_channels=self.time_channels,
                    attn=False,
                    n_groups=n_groups,
                ),
            ]
        )
        # print("Mid")

        self.up_block = nn.ModuleList()
        for idx in list(reversed(range(1, len(channels)))):
            out_channels = in_channels
            attn = attns[idx - 1]
            for _ in range(n_blocks):
                # print("Res", in_channels, out_channels)
                self.up_block.append(
                    ResBlock(
                        in_channels=in_channels + out_channels,
                        out_channels=out_channels,
                        time_channels=self.time_channels,
                        attn=attn,
                        n_groups=n_groups,
                    )
                )
            in_channels = channels[idx]
            out_channels = channels[idx - 1]
            # print("Res", in_channels, out_channels)
            self.up_block.append(
                ResBlock(
                    in_channels=in_channels + out_channels,
                    out_channels=out_channels,
                    time_channels=self.time_channels,
                    attn=attn,
                    n_groups=n_groups,
                )
            )
            in_channels = out_channels

            if idx > 1:
                # print("Up", out_channels)
                self.up_block.append(Upsample(out_channels))

        self.fin_block = nn.Sequential(
            nn.GroupNorm(n_groups, out_channels),
            Swish(),
            nn.Conv2d(out_channels, 3, 3, 1, 1)
        )

    def forward(self, noisy_image, diffusion_step):
        x = self.init_conv(noisy_image)
        # print(x.shape)
        t = self.time_embed(diffusion_step)
        # print(t.shape)

        xs = [x]
        for layer in self.down_block:
            if isinstance(layer, Downsample):
                x = layer(x)
            else:
                x = layer(x, t)
            # print(x.shape)
            xs.append(x)

        for layer in self.mid_block:
            x = layer(x, t)
        # print(x.shape)

        for layer in self.up_block:
            if isinstance(layer, Upsample):
                x = layer(x)
            else:
                x = torch.cat([x, xs.pop()], dim=1)
                x = layer(x, t)
            # print(x.shape)
        assert len(xs) == 0

        x = self.fin_block(x)
        # print(x.shape)
        return x


class DDPM(nn.Module):
    def get_linear_beta_schdule(self):
        # "We set the forward process variances to constants increasing linearly."
        # return torch.linspace(init_beta, fin_beta, n_diffusion_steps) # "$\beta_{t}$"
        return torch.linspace(
            self.init_beta,
            self.fin_beta,
            self.n_diffusion_steps,
            device=self.device,
        ) # "$\beta_{t}$"

    # "We set T = 1000 without a sweep."
    # "We chose a linear schedule from $\beta_{1} = 10^{-4}$ to  $\beta_{T} = 0:02$."
    def __init__(
        self,
        img_size,
        init_channels,
        channels,
        attns,
        device,
        n_blocks,
        n_channels=3,
        n_diffusion_steps=1000,
        init_beta=0.0001,
        fin_beta=0.02,
    ):
        super().__init__()

        self.img_size = img_size
        self.device = device
        self.n_channels = n_channels
        self.n_diffusion_steps = n_diffusion_steps
        self.init_beta = init_beta
        self.fin_beta = fin_beta

        self.beta = self.get_linear_beta_schdule()
        self.alpha = 1 - self.beta # "$\alpha_{t} = 1 - \beta_{t}$"
        # "$\bar{\alpha_{t}} = \prod^{t}_{s=1}{\alpha_{s}}$"
        self.alpha_bar = torch.cumprod(self.alpha, dim=0)

        self.net = UNet(
            n_diffusion_steps=n_diffusion_steps,
            init_channels=init_channels,
            channels=channels,
            attns=attns,
            n_blocks=n_blocks,
        ).to(device)

    @staticmethod
    def index(x, diffusion_step):
        return torch.index_select(x, dim=0, index=diffusion_step)[:, None, None, None]

    def sample_noise(self, batch_size):
        return torch.randn(
            size=(batch_size, self.n_channels, self.img_size, self.img_size),
            device=self.device,
        )

    def sample_diffusion_step(self, batch_size):
        return torch.randint(
            0, self.n_diffusion_steps, size=(batch_size,), device=self.device,
        )
        # return torch.randint(
        #     0, self.n_diffusion_steps, size=(1,), device=self.device,
        # ).repeat(batch_size)

    def batchify_diffusion_steps(self, diffusion_step_idx, batch_size):
        return torch.full(
            size=(batch_size,),
            fill_value=diffusion_step_idx,
            dtype=torch.long,
            device=self.device,
        )  

    def perform_diffusion_process(self, ori_image, diffusion_step, random_noise=None):
        # "$\bar{\alpha_{t}}$"
        alpha_bar_t = self.index(self.alpha_bar, diffusion_step=diffusion_step)
        mean = (alpha_bar_t ** 0.5) * ori_image # $\sqrt{\bar{\alpha_{t}}}x_{0}$
        var = 1 - alpha_bar_t # $(1 - \bar{\alpha_{t}})\mathbf{I}$
        if random_noise is None:
            random_noise = self.sample_noise(batch_size=ori_image.size(0))
        noisy_image = mean + (var ** 0.5) * random_noise
        return noisy_image

    def forward(self, noisy_image, diffusion_step):
        return self.net(noisy_image=noisy_image, diffusion_step=diffusion_step)

    def get_loss(self, ori_image):
        # "Algorithm 1-3: $t \sim Uniform(\{1, \ldots, T\})$"
        diffusion_step = self.sample_diffusion_step(batch_size=ori_image.size(0))
        random_noise = self.sample_noise(batch_size=ori_image.size(0))
        # "Algorithm 1-4: $\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$"
        noisy_image = self.perform_diffusion_process(
            ori_image=ori_image, diffusion_step=diffusion_step, random_noise=random_noise,
        )
        with torch.autocast(
            device_type=self.device.type, dtype=torch.float16,
        ) if self.device.type == "cuda" else contextlib.nullcontext():
            pred_noise = self(noisy_image=noisy_image, diffusion_step=diffusion_step)
            return F.mse_loss(pred_noise, random_noise, reduction="mean")

    @torch.inference_mode()
    def take_denoising_step(self, noisy_image, diffusion_step_idx):
        diffusion_step = self.batchify_diffusion_steps(
            diffusion_step_idx=diffusion_step_idx, batch_size=noisy_image.size(0),
        )
        alpha_t = self.index(self.alpha, diffusion_step=diffusion_step)
        beta_t = self.index(self.beta, diffusion_step=diffusion_step)
        alpha_bar_t = self.index(self.alpha_bar, diffusion_step=diffusion_step)
        pred_noise = self(noisy_image=noisy_image.detach(), diffusion_step=diffusion_step)
        # # "Algorithm 2-4:
        # $x_{t - 1} = \frac{1}{\sqrt{\alpha_{t}}}
        # \Big(x_{t} - \frac{\beta_{t}}{\sqrt{1 - \bar{\alpha_{t}}}}z_{\theta}(x_{t}, t)\Big)
        # + \sigma_{t}z"$
        mean = (1 / (alpha_t ** 0.5)) * (
            noisy_image - ((beta_t / ((1 - alpha_bar_t) ** 0.5)) * pred_noise)
        )
        if diffusion_step_idx > 0:
            var = beta_t
            random_noise = self.sample_noise(batch_size=noisy_image.size(0))
            denoised_image = mean + (var ** 0.5) * random_noise
        else:
            denoised_image = mean
        return denoised_image

    @torch.inference_mode()
    def perform_denoising_process(self, noisy_image, diffusion_step_idx):
        x = noisy_image
        pbar = tqdm(range(diffusion_step_idx, -1, -1), leave=False)
        for trg_diffusion_step in pbar:
            pbar.set_description("Denoising...")

            x = self.take_denoising_step(x, diffusion_step_idx=trg_diffusion_step)
        return x

    @torch.inference_mode()
    def sample(self, batch_size):
        random_noise = self.sample_noise(batch_size=batch_size) # "$x_{T}$"
        return self.perform_denoising_process(
            noisy_image=random_noise, diffusion_step_idx=self.n_diffusion_steps - 1,
        )

    @torch.inference_mode()
    def reconstruct(self, noisy_image, noise, diffusion_step):
        alpha_bar_t = self.index(self.alpha_bar, diffusion_step=diffusion_step)
        return (noisy_image - ((1 - alpha_bar_t) ** 0.5) * noise) / (alpha_bar_t ** 0.5)

    @staticmethod
    def _get_frame(x):
        b, _, _, _ = x.shape
        grid = image_to_grid(x, n_cols=int(b ** 0.5))
        frame = np.array(grid)
        return frame

    def vis_denoising_process(self, batch_size, save_path, n_frames=100):
        with imageio.get_writer(save_path, mode="I") as writer:
            x = self.sample_noise(batch_size=batch_size)
            for diffusion_step_idx in range(self.n_diffusion_steps, 0, -1):
                x = self.take_denoising_step(x, diffusion_step_idx=diffusion_step_idx)

                if diffusion_step_idx % (self.n_diffusion_steps // n_frames) == 0:
                    frame = self._get_frame(x)
                    writer.append_data(frame)

    @staticmethod
    def _interpolate_between_images(x, y, n_points):
        _, b, c, d = x.shape
        lambs = torch.linspace(start=0, end=1, steps=n_points)
        lambs = lambs[:, None, None, None].expand(n_points, b, c, d)
        return ((1 - lambs) * x + lambs * y)

    def interpolate(
        self, ori_image1, ori_image2, interpolate_at=500, n_points=10,
    ):
        diffusion_step = self.batchify_diffusion_steps(interpolate_at, batch_size=1)
        noisy_image1 = self.perform_diffusion_process(
            ori_image=ori_image1, diffusion_step=diffusion_step,
        )
        noisy_image2 = self.perform_diffusion_process(
            ori_image=ori_image2, diffusion_step=diffusion_step,
        )

        x = self._interpolate_between_images(noisy_image1, noisy_image2, n_points=n_points)
        for diffusion_step_idx in range(interpolate_at, 0, -1):
            x = self.take_denoising_step(x, diffusion_step_idx=diffusion_step_idx)
        return torch.cat([ori_image1, x, ori_image2], dim=0)

    def coarse_to_fine_interpolate(self, ori_image1, ori_image2, n_rows=9, n_points=10):
        rows = list()
        for interpolate_at in range(
            self.n_diffusion_steps, -1, - self.n_diffusion_steps // (n_rows - 1),
        ):
            row = self.interpolate(
                ori_image1=ori_image1,
                ori_image2=ori_image2,
                interpolate_at=interpolate_at,
                n_points=n_points,
            )
            rows.append(row)
        return torch.cat(rows, dim=0)


if __name__ == "__main__":
    new = UNet(
        n_diffusion_steps=1000,
        init_channels=128,
        channels=(128, 256, 256, 256),
        attns=(True, True, True, True),
        n_blocks=2,
    )
    # print_n_params(new)
    x = torch.randn(1, 3, 32, 32)
    t = torch.randint(0, 1000, (1,))
    new(x, t)
