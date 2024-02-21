# References:
    # https://github.com/w86763777/pytorch-ddpm/blob/master/model.py
    # https://huggingface.co/blog/annotated-diffusion

import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
import imageio
import math
from tqdm import tqdm
from pathlib import Path

from utils import image_to_grid


class Swish(nn.Module):
    def forward(self, x):
        return x * torch.sigmoid(x)


# class TimeEmbedding(nn.Module):
#     def __init__(self, n_diffusion_steps, d_model, dim):
#         assert d_model % 2 == 0
#         super().__init__()

#         emb = torch.arange(0, d_model, step=2) / d_model * math.log(10000)
#         emb = torch.exp(-emb)
#         pos = torch.arange(n_diffusion_steps).float()
#         emb = pos[:, None] * emb[None, :]
#         assert list(emb.shape) == [n_diffusion_steps, d_model // 2]
#         emb = torch.stack([torch.sin(emb), torch.cos(emb)], dim=-1)
#         assert list(emb.shape) == [n_diffusion_steps, d_model // 2, 2]
#         emb = emb.view(n_diffusion_steps, d_model)

#         self.timembedding = nn.Sequential(
#             nn.Embedding.from_pretrained(emb),
#             nn.Linear(d_model, dim),
#             Swish(),
#             nn.Linear(dim, dim),
#         )

#     def forward(self, t):
#         emb = self.timembedding(t)
#         return emb
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

        self.register_buffer("pos_enc_mat", self.pe_mat)

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


# class ResConvSelfAttnBlock(nn.Module):
#     def __init__(self, in_ch):
#         super().__init__()
#         self.group_norm = nn.GroupNorm(32, in_ch)
#         self.proj_q = nn.Conv2d(in_ch, in_ch, 1, stride=1, padding=0)
#         self.proj_k = nn.Conv2d(in_ch, in_ch, 1, stride=1, padding=0)
#         self.proj_v = nn.Conv2d(in_ch, in_ch, 1, stride=1, padding=0)
#         self.proj = nn.Conv2d(in_ch, in_ch, 1, stride=1, padding=0)

#     def forward(self, x):
#         B, C, H, W = x.shape
#         h = self.group_norm(x)
#         q = self.proj_q(h)
#         k = self.proj_k(h)
#         v = self.proj_v(h)

#         q = q.permute(0, 2, 3, 1).view(B, H * W, C)
#         k = k.view(B, C, H * W)
#         w = torch.bmm(q, k) * (int(C) ** (-0.5))
#         w = F.softmax(w, dim=-1)

#         v = v.permute(0, 2, 3, 1).view(B, H * W, C)
#         h = torch.bmm(w, v)
#         h = h.view(B, H, W, C).permute(0, 3, 1, 2)
#         h = self.proj(h)
#         return x + h
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
    def __init__(self, in_ch, out_ch, tdim, dropout, attn=False):
        super().__init__()
        self.block1 = nn.Sequential(
            nn.GroupNorm(32, in_ch),
            Swish(),
            nn.Conv2d(in_ch, out_ch, 3, stride=1, padding=1),
        )
        self.temb_proj = nn.Sequential(
            Swish(),
            nn.Linear(tdim, out_ch),
        )
        self.block2 = nn.Sequential(
            nn.GroupNorm(32, out_ch),
            Swish(),
            nn.Dropout(dropout),
            nn.Conv2d(out_ch, out_ch, 3, stride=1, padding=1),
        )
        if in_ch != out_ch:
            self.shortcut = nn.Conv2d(in_ch, out_ch, 1, stride=1, padding=0)
        else:
            self.shortcut = nn.Identity()
        if attn:
            self.attn = ResConvSelfAttnBlock(out_ch)
        else:
            self.attn = nn.Identity()

    def forward(self, x, temb):
        h = self.block1(x)
        h = h + self.temb_proj(temb)[:, :, None, None]
        h = self.block2(h)

        h = h + self.shortcut(x)
        h = self.attn(h)
        return h


class OldUNet(nn.Module):
    def __init__(self, n_diffusion_steps=1000, ch=128, ch_mult=[1, 2, 2, 2], attn=[1], num_res_blocks=2, dropout=0.1):
        super().__init__()

        assert all([i < len(ch_mult) for i in attn]), "attn index out of bound"

        tdim = ch * 4
        self.time_embedding = TimeEmbedding(
            # n_diffusion_steps=n_diffusion_steps, d_model=ch, dim=tdim,
            n_diffusion_steps=n_diffusion_steps, time_channels=tdim,
        )

        self.head = nn.Conv2d(3, ch, kernel_size=3, stride=1, padding=1)
        self.downblocks = nn.ModuleList()
        cxs = [ch]  # record output channel when dowmsample for upsample
        cur_ch = ch
        for i, mult in enumerate(ch_mult):
            out_ch = ch * mult
            for _ in range(num_res_blocks):
                self.downblocks.append(
                    ResBlock(
                        in_ch=cur_ch,
                        out_ch=out_ch,
                        tdim=tdim,
                        dropout=dropout,
                        attn=(i in attn)
                    )
                )
                cur_ch = out_ch
                cxs.append(cur_ch)
            if i != len(ch_mult) - 1:
                self.downblocks.append(Downsample(cur_ch))
                cxs.append(cur_ch)

        self.middleblocks = nn.ModuleList([
            ResBlock(cur_ch, cur_ch, tdim, dropout, attn=True),
            ResBlock(cur_ch, cur_ch, tdim, dropout, attn=False),
        ])

        self.upblocks = nn.ModuleList()
        for i, mult in reversed(list(enumerate(ch_mult))):
            out_ch = ch * mult
            for _ in range(num_res_blocks + 1):
                self.upblocks.append(
                ResBlock(
                    in_ch=cxs.pop() + cur_ch,
                    out_ch=out_ch,
                    tdim=tdim,
                    dropout=dropout,
                    attn=(i in attn))
                )
                cur_ch = out_ch
            if i != 0:
                self.upblocks.append(Upsample(cur_ch))
        assert len(cxs) == 0

        self.tail = nn.Sequential(
            nn.GroupNorm(32, cur_ch),
            Swish(),
            nn.Conv2d(cur_ch, 3, kernel_size=3, stride=1, padding=1)
        )

    def forward(self, noisy_image, diffusion_step):
        temb = self.time_embedding(diffusion_step)
        x = self.head(noisy_image)
        xs = [x]
        for layer in self.downblocks:
            if isinstance(layer, Downsample):
                x = layer(x)
            else:
                x = layer(x, temb)
            xs.append(x)

        for layer in self.middleblocks:
            x = layer(x, temb)

        for layer in self.upblocks:
            if isinstance(layer, ResBlock):
                x = torch.cat([x, xs.pop()], dim=1)

            if isinstance(layer, Upsample):
                x = layer(x)
            else:
                x = layer(x, temb)
        x = self.tail(x)
        assert len(xs) == 0
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

        # self.net = UNet(
        #     n_diffusion_steps=n_diffusion_steps,
        #     init_channels=init_channels,
        #     channels=channels,
        #     attns=attns,
        #     n_blocks=n_blocks,
        # ).to(device)
        self.net = OldUNet().to(device)
        # self.net = labmlUNet().to(device)

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

    def batchify_diffusion_steps(self, cur_diffusion_step, batch_size):
        return torch.full(
            size=(batch_size,),
            fill_value=cur_diffusion_step,
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
        # return self.net(noisy_image=noisy_image, diffusion_step=diffusion_step)
        return self.net(noisy_image, diffusion_step)

    def get_loss(self, ori_image):
        # "Algorithm 1-3: $t \sim Uniform(\{1, \ldots, T\})$"
        diffusion_step = self.sample_diffusion_step(batch_size=ori_image.size(0))
        random_noise = self.sample_noise(batch_size=ori_image.size(0))
        # "Algorithm 1-4: $\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$"
        noisy_image = self.perform_diffusion_process(
            ori_image=ori_image, diffusion_step=diffusion_step, random_noise=random_noise,
        )
        pred_noise = self(noisy_image=noisy_image, diffusion_step=diffusion_step)
        # recon_image = self.reconstruct(
        #     noisy_image=noisy_image, noise=pred_noise.detach(), diffusion_step=diffusion_step,
        # )
        # image_to_grid(recon_image, n_cols=int(recon_image.size(0) ** 0.5)).show()
        return F.mse_loss(pred_noise, random_noise, reduction="mean")

    @torch.inference_mode()
    def reconstruct(self, noisy_image, noise, diffusion_step):
        alpha_bar_t = self.index(self.alpha_bar, diffusion_step=diffusion_step)
        return (noisy_image - ((1 - alpha_bar_t) ** 0.5) * noise) / (alpha_bar_t ** 0.5)

    @torch.inference_mode()
    def take_denoising_step(self, noisy_image, cur_diffusion_step):
        diffusion_step = self.batchify_diffusion_steps(
            cur_diffusion_step=cur_diffusion_step, batch_size=noisy_image.size(0),
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
        # mean = (1 / (alpha_t ** 0.5)) * (noisy_image - (1 - alpha_t) / ((1 - alpha_bar_t) ** 0.5) * pred_noise)
        if cur_diffusion_step > 0:
            var = beta_t
            random_noise = self.sample_noise(batch_size=noisy_image.size(0))
            denoised_image = mean + (var ** 0.5) * random_noise
        else:
            denoised_image = mean
        # denoised_image.clamp_(-1, 1)
        return denoised_image

    @torch.inference_mode()
    def perform_denoising_process(self, noisy_image, cur_diffusion_step):
        x = noisy_image
        pbar = tqdm(range(cur_diffusion_step, -1, -1), leave=False)
        for trg_diffusion_step in pbar:
            pbar.set_description("Denoising...")

            x = self.take_denoising_step(x, cur_diffusion_step=trg_diffusion_step)
        return x

    @torch.inference_mode()
    def sample(self, batch_size):
        random_noise = self.sample_noise(batch_size=batch_size) # "$x_{T}$"
        return self.perform_denoising_process(
            noisy_image=random_noise, cur_diffusion_step=self.n_diffusion_steps - 1,
        )
