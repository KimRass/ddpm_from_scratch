# References:
    # https://medium.com/mlearning-ai/enerating-images-with-ddpms-a-pytorch-implementation-cef5a2ba8cb1

import torch
from torch.optim import Adam
from torch.utils.data import DataLoader
from torch.cuda.amp import GradScaler
import argparse
from pathlib import Path
import math
from time import time
import wandb

from utils import (
    set_seed,
    get_config,
    sample_noise,
    sample_t,
    get_elapsed_time,
    modify_state_dict,
)
from celeba import CelebADataset
from model import DDPM


def get_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--run_id", type=str, required=False)
    parser.add_argument("--data_dir", type=str, required=True)
    parser.add_argument("--n_epochs", type=int, required=True)
    parser.add_argument("--batch_size", type=int, required=True)
    parser.add_argument("--lr", type=float, required=True)
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


def train_single_step(ori_image, model, optim, scaler, config):
    ori_image = ori_image.to(config["DEVICE"])
    with torch.autocast(
        device_type=config["DEVICE"].type,
        dtype=torch.float16 if config["DEVICE"].type == "cuda" else torch.bfloat16,
    ):
        loss = model.get_loss(ori_image)

    optim.zero_grad()
    if scaler is not None:
        scaler.scale(loss).backward()
        scaler.step(optim)
        scaler.update()
    else:
        loss.backward()
        optim.step()
    return loss


def save_wandb_checkpoint(epoch, model, scaler, optim, loss, save_path):
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    state_dict = {
        "epoch": epoch,
        "diffusion_model": modify_state_dict(model.state_dict()),
        "scaler": scaler.state_dict(),
        "optimizer": optim.state_dict(),
        "loss": loss,
    }
    torch.save(state_dict, str(save_path))
    wandb.save(str(save_path), base_path=Path(save_path).parent)


def save_diffusion_model(model, save_path):
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(modify_state_dict(model.state_dict()), str(save_path))


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
    args = get_args()
    CONFIG = get_config(config_path=Path(__file__).parent/"configs/flickr.yaml", args=args)
    set_seed(CONFIG["SEED"])
    init_wandb(run_id=CONFIG["RUN_ID"], img_size=CONFIG["IMG_SIZE"])

    train_dl = get_tain_dl(
        data_dir=CONFIG["DATA_DIR"],
        img_size=CONFIG["IMG_SIZE"],
        batch_size=CONFIG["BATCH_SIZE"],
        n_cpus=CONFIG["N_CPUS"],
    )

    model = DDPM(
        n_timesteps=CONFIG["N_TIMESTEPS"],
        init_beta=CONFIG["INIT_BETA"],
        fin_beta=CONFIG["FIN_BETA"],
    ).to(CONFIG["DEVICE"])
    if CONFIG["TORCH_COMPILE"]:
        model = torch.compile(model)
    optim = Adam(model.parameters(), lr=CONFIG["LR"])
    scaler = GradScaler() if CONFIG["DEVICE"].type == "cuda" else None

    if wandb.run.resumed:
        state_dict = torch.load(
            str(wandb.restore(CONFIG["WANDB_CKPT_PATH"])), map_location=CONFIG["DEVICE"],
        )
        model.load_state_dict(state_dict["diffusion_model"])
        optim.load_state_dict(state_dict["optimizer"])
        scaler.load_state_dict(state_dict["scaler"])
        init_epoch = state_dict["epoch"]
        min_loss = state_dict["loss"]
        print(f"Resuming from epoch {init_epoch + 1}...")
    else:
        init_epoch = 0
        min_loss = math.inf

    n_cols = int(CONFIG["BATCH_SIZE"] ** 0.5)
    for epoch in range(init_epoch + 1, CONFIG["N_EPOCHS"] + 1):
        cum_loss = 0
        start_time = time()
        for ori_image in train_dl: # "$x_{0} \sim q(x_{0})$"
            loss = train_single_step(
                ori_image=ori_image, model=model, optim=optim, scaler=scaler, config=CONFIG,
            )
            cum_loss += loss.item()
        cur_loss = cum_loss / len(train_dl)

        if cur_loss < min_loss:
            min_loss = cur_loss

        msg = f"[ {get_elapsed_time(start_time)} ]"
        msg += f"""[ {epoch}/{CONFIG["N_EPOCHS"]} ]"""
        msg += f"[ Min loss: {min_loss:.5f} ]"
        msg += f"[ Loss: {cur_loss:.5f} ]"
        print(msg)

        wandb.log({"Min loss": min_loss, "Loss": cur_loss}, step=epoch)

        filename = f"""DDPM_{CONFIG["IMG_SIZE"]}×{CONFIG["IMG_SIZE"]}_epoch_{epoch}.pth"""
        save_diffusion_model(model=model, save_path=CONFIG["CKPTS_DIR"]/filename)

        save_wandb_checkpoint(
            epoch=epoch,
            model=model,
            scaler=scaler,
            optim=optim,
            loss=cur_loss,
            save_path=CONFIG["WANDB_CKPT_PATH"],
        )
