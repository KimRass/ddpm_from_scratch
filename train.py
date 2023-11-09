# References:
    # https://medium.com/mlearning-ai/enerating-images-with-ddpms-a-pytorch-implementation-cef5a2ba8cb1

import torch
import torch.nn as nn
from torch.optim import Adam
from torch.utils.data import DataLoader
from torch.cuda.amp import GradScaler
import argparse
from pathlib import Path
import math
from time import time
import wandb
from tqdm import tqdm

from utils import (
    set_seed,
    get_config,
    sample_noise,
    sample_t,
    get_elapsed_time,
    modify_state_dict,
)
from celeba import CelebADataset
from ddpm import DDPM
from evaluate import Evaluator


def _get_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--run_id", type=str, required=True)
    parser.add_argument("--data_dir", type=str, required=True)
    parser.add_argument("--n_epochs", type=int, required=True)
    parser.add_argument("--batch_size", type=int, required=True)
    parser.add_argument("--lr", type=float, required=True)
    parser.add_argument("--img_size", type=int, required=False, default=32)
    parser.add_argument("--n_cpus", type=int, required=False, default=0)
    parser.add_argument("--torch_compile", action="store_true", required=False)

    args = parser.parse_args()
    return args


def init_wandb(run_id, img_size):
    if run_id is None:
        run_id = wandb.util.generate_id()
    wandb.init(project="DDPM", resume="allow", id=run_id)
    wandb.config.update({"IMG_SIZE": img_size})
    print(wandb.config)


def train_single_step(x0, ddpm, optim, scaler, crit, config):
    x0 = x0.to(config["DEVICE"])

    t = sample_t(
        n_timesteps=config["N_TIMESTEPS"], batch_size=config["BATCH_SIZE"], device=config["DEVICE"],
    ) # "$t \sim Uniform({1, \ldots, T})$"
    eps = sample_noise(
        batch_size=config["BATCH_SIZE"],
        n_channels=config["N_CHANNELS"],
        img_size=config["IMG_SIZE"],
        device=config["DEVICE"],
    ) # "$\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$"

    with torch.autocast(
        device_type=config["DEVICE"].type,
        dtype=torch.float16 if config["DEVICE"].type == "cuda" else torch.bfloat16,
    ):
        noisy_image = ddpm(x0, t=t, eps=eps)
        pred_eps = ddpm.predict_noise(x=noisy_image, t=t)
        loss = crit(pred_eps, eps)

    optim.zero_grad()
    if scaler is not None:
        scaler.scale(loss).backward()
        scaler.step(optim)
        scaler.update()
    else:
        loss.backward()
        optim.step()
    return loss


# def save_checkpoint(epoch, ddpm, scaler, optim, loss, save_path):
# # def save_checkpoint(ddpm, save_path):
#     Path(save_path).parent.mkdir(parents=True, exist_ok=True)
#     state_dict = {
#         "epoch": epoch,
#         "ddpm": modify_state_dict(ddpm.state_dict()),
#         "scaler": scaler.state_dict(),
#         "optimizer": optim.state_dict(),
#         "loss": loss,
#     }
#     torch.save(state_dict, str(save_path))
#     # torch.save(modify_state_dict(ddpm.state_dict()), str(save_path))
#     wandb.save(str(save_path), base_path=Path(save_path).parent)


def save_wandb_checkpoint(epoch, ddpm, scaler, optim, loss, save_path):
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    state_dict = {
        "epoch": epoch,
        "ddpm": modify_state_dict(ddpm.state_dict()),
        "scaler": scaler.state_dict(),
        "optimizer": optim.state_dict(),
        "loss": loss,
    }
    torch.save(state_dict, str(save_path))
    # torch.save(modify_state_dict(ddpm.state_dict()), str(save_path))
    wandb.save(str(save_path), base_path=Path(save_path).parent)


def save_ddpm(ddpm, save_path):
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(modify_state_dict(ddpm.state_dict()), str(save_path))


def get_tain_dl(data_dir, img_size, batch_size, n_cpus):
    train_ds = CelebADataset(data_dir=data_dir, img_size=img_size)
    train_dl = DataLoader(
        train_ds,
        batch_size=batch_size,
        shuffle=True,
        num_workers=n_cpus,
        pin_memory=True,
        drop_last=True,
    )
    print(f"Number of train data samples: {len(train_ds):,}")
    return train_dl


if __name__ == "__main__":
    args = _get_args()
    CONFIG = get_config(args)
    set_seed(CONFIG["SEED"])
    init_wandb(run_id=CONFIG["RUN_ID"], img_size=CONFIG["IMG_SIZE"])

    train_dl = get_tain_dl(
        data_dir=CONFIG["DATA_DIR"],
        img_size=CONFIG["IMG_SIZE"],
        batch_size=CONFIG["BATCH_SIZE"],
        n_cpus=CONFIG["N_CPUS"],
    )

    ddpm = DDPM(
        n_timesteps=CONFIG["N_TIMESTEPS"],
        init_beta=CONFIG["INIT_BETA"],
        fin_beta=CONFIG["FIN_BETA"],
    ).to(CONFIG["DEVICE"])
    if CONFIG["TORCH_COMPILE"]:
        ddpm = torch.compile(ddpm)
    optim = Adam(ddpm.parameters(), lr=CONFIG["LR"])
    scaler = GradScaler() if CONFIG["DEVICE"].type == "cuda" else None
    crit = nn.MSELoss(reduction="mean")

    evaluator = Evaluator(
        n_samples=CONFIG["N_EVAL_IMAGES"], n_cpus=CONFIG["N_CPUS"], dl=train_dl, device=CONFIG["DEVICE"],
    )

    if wandb.run.resumed:
        # state_dict = torch.load(str(CONFIG["CKPT_TAR_PATH"]), map_location=CONFIG["DEVICE"])
        state_dict = torch.load(
            str(wandb.restore(CONFIG["CKPT_TAR_PATH"])), map_location=CONFIG["DEVICE"],
        )
        ddpm.load_state_dict(state_dict["ddpm"])
        optim.load_state_dict(state_dict["optimizer"])
        scaler.load_state_dict(state_dict["scaler"])
        init_epoch = state_dict["epoch"]
        # min_loss = state_dict["loss"]
        best_fid = state_dict["fid"]
        print(f"Resuming from epoch {init_epoch + 1}...")
    else:
        init_epoch = 0
        # min_loss = math.inf
        best_fid = math.inf

    n_cols = int(CONFIG["BATCH_SIZE"] ** 0.5)
    # for epoch in range(init_epoch + 1, CONFIG["N_EPOCHS"] + 1):
    for epoch in tqdm(range(init_epoch + 1, CONFIG["N_EPOCHS"] + 1)):
        accum_loss = 0
        start_time = time()
        for x0 in train_dl: # "$x_{0} \sim q(x_{0})$"
            loss = train_single_step(
                x0=x0, ddpm=ddpm, optim=optim, scaler=scaler, crit=crit, config=CONFIG,
            )
            accum_loss += loss.item()

        ### Evaluate.
        cur_fid = evaluator.evaluate(ddpm=ddpm, device=CONFIG["DEVICE"])

        msg = f"""[ {epoch}/{CONFIG["N_EPOCHS"]} ]"""
        msg += f"[ {get_elapsed_time(start_time)} ]"
        msg += f"[ Loss: {accum_loss:.5f} ]"
        msg += f"[ FID: {cur_fid:.3f} ]"

        wandb.log({"Loss": accum_loss, "FID": cur_fid}, step=epoch)

        if cur_fid < best_fid:
            filename = f"""{CONFIG["IMG_SIZE"]}×{CONFIG["IMG_SIZE"]}_epoch_{epoch}.pth"""
            save_ddpm(
                ddpm=ddpm,
                save_path=CONFIG["CKPTS_DIR"]/filename,
            )
            msg += f" (Saved checkpoint.)"
            best_fid = cur_fid
        print(msg)

        save_wandb_checkpoint(
            epoch=epoch,
            ddpm=ddpm,
            scaler=scaler,
            optim=optim,
            loss=accum_loss,
            save_path=CONFIG["CKPT_TAR_PATH"],
        )
