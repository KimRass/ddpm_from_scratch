# References:
    # https://wandb.ai/wandb_fc/korean/reports/-Frechet-Inception-distance-FID-GANs---Vmlldzo0MzQ3Mzc
    # https://github.com/w86763777/pytorch-ddpm/blob/master/score/fid.py

import torch
from torch.utils.data import DataLoader
import numpy as np
import scipy
from tqdm import tqdm
import math
import argparse

from utils import get_config
from inceptionv3 import InceptionV3
from generate_image import get_ddpm_from_checkpoint
from celeba import CelebADataset, ImageGridDataset


def get_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--ckpt_path", type=str, required=True)
    parser.add_argument("--real_data_dir", type=str, required=True)
    parser.add_argument("--gen_data_dir", type=str, required=True)
    parser.add_argument("--batch_size", type=int, required=True)
    parser.add_argument("--n_eval_imgs", type=int, required=True)
    parser.add_argument("--n_cpus", type=int, required=False, default=0)

    args = parser.parse_args()
    return args


def get_matrix_sqrt(x):
    sqrtm = scipy.linalg.sqrtm(x)
    if np.iscomplexobj(sqrtm):
       sqrtm = sqrtm.real
    return sqrtm


def get_mean_and_cov(embed):
    mu = embed.mean(axis=0)
    sigma = np.cov(embed, rowvar=False)
    return mu, sigma


def get_frechet_distance(mu1, mu2, sigma1, sigma2):
    cov_product = get_matrix_sqrt(sigma1 @ sigma2)
    fd = ((mu1 - mu2) ** 2).sum() - np.trace(sigma1 + sigma2 - 2 * cov_product)
    return fd.item()


def get_fid(embed1, embed2):
    mu1, sigma1 = get_mean_and_cov(embed1)
    mu2, sigma2 = get_mean_and_cov(embed2)
    fd = get_frechet_distance(mu1=mu1, mu2=mu2, sigma1=sigma1, sigma2=sigma2)
    return fd


class Evaluator(object):
    def __init__(self, ddpm, n_eval_imgs, batch_size, real_dl, gen_dl, device):

        self.ddpm = ddpm
        self.ddpm.eval()
        self.n_eval_imgs = n_eval_imgs
        self.batch_size = batch_size
        self.real_dl = real_dl
        self.gen_dl = gen_dl
        self.device = device

        self.inceptionv3 = InceptionV3().to(device)
        self.inceptionv3.eval()

        self.real_embed = self.get_real_embedding()

    def _to_embeddding(self, x):
        embed = self.inceptionv3(x.detach())
        embed = embed.squeeze()
        embed = embed.cpu().numpy()
        return embed

    @torch.no_grad()
    def get_real_embedding(self):
        embeds = list()
        di = iter(self.real_dl)
        for _ in range(math.ceil(self.n_eval_imgs // self.batch_size)):
            x0 = next(di)
            _, self.n_channels, self.img_size, _ = x0.shape
            x0 = x0.to(self.device)
            embed = self._to_embeddding(x0)
            embeds.append(embed)
        real_embed = np.concatenate(embeds)[: self.n_eval_imgs]
        return real_embed

    @torch.no_grad()
    def get_real_embedding(self):
        embeds = list()
        di = iter(self.real_dl)
        for _ in range(math.ceil(self.n_eval_imgs // self.batch_size)):
            x0 = next(di)
            _, self.n_channels, self.img_size, _ = x0.shape
            x0 = x0.to(self.device)
            embed = self._to_embeddding(x0)
            embeds.append(embed)
        gen_embed = np.concatenate(embeds)[: self.n_eval_imgs]
        return gen_embed

    # @torch.no_grad()
    # def get_generated_embedding(self):
    #     print("Calculating embeddings for synthetic data distribution...")

    #     embeds = list()
    #     for _ in tqdm(range(math.ceil(self.n_eval_imgs // self.batch_size))):
    #         x0 = self.ddpm.sample(
    #             batch_size=self.batch_size,
    #             n_channels=self.n_channels,
    #             img_size=self.img_size,
    #             device=self.device,
    #             to_image=False,
    #         )
    #         embed = self._to_embeddding(x0)
    #         embeds.append(embed)
    #     synth_embed = np.concatenate(embeds)[: self.n_eval_imgs]
    #     return synth_embed

    def evaluate(self):
        synth_embed = self.get_generated_embedding()
        fid = get_fid(self.real_embed, synth_embed)
        return fid


if __name__ == "__main__":
    args = get_args()
    CONFIG = get_config(args)

    real_ds = CelebADataset(data_dir=CONFIG["REAL_DATA_DIR"], img_size=CONFIG["IMG_SIZE"])
    real_dl = DataLoader(
        real_ds,
        batch_size=CONFIG["BATCH_SIZE"],
        shuffle=True,
        num_workers=CONFIG["N_CPUS"],
        pin_memory=True,
        drop_last=True,
    )
    gen_ds = ImageGridDataset(
        data_dir=CONFIG["GEN_DATA_DIR"],
        img_size=CONFIG["IMG_SIZE"],
    )
    gen_dl = DataLoader(
        gen_ds,
        batch_size=CONFIG["BATCH_SIZE"],
        shuffle=True,
        num_workers=CONFIG["N_CPUS"],
        pin_memory=True,
        drop_last=True,
    )

    ddpm = get_ddpm_from_checkpoint(
        ckpt_path=CONFIG["CKPT_PATH"],
        n_timesteps=CONFIG["N_TIMESTEPS"],
        init_beta=CONFIG["INIT_BETA"],
        fin_beta=CONFIG["FIN_BETA"],
        device=CONFIG["DEVICE"],
    )

    evaluator = Evaluator(
        ddpm=ddpm,
        n_eval_imgs=CONFIG["N_EVAL_IMGS"],
        batch_size=CONFIG["BATCH_SIZE"],
        real_dl=real_dl,
        gen_dl=gen_dl,
        device=CONFIG["DEVICE"],
    )
    fid = evaluator.evaluate()
    print(f"Frechet instance distance: {fid:.2f}")
