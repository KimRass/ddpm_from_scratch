# 1. Pre-trained Models
| | | |
|-|-|-|
| [ddpm_celeba_32×32.pth](https://drive.google.com/file/d/10nYTU1NNv3GghPwb8Mgp29Seni6iTI1a/view?usp=sharing) | Trained on CelebA 32 × 32 |
| [ddpm_celeba_64×64.pth](https://drive.google.com/file/d/1S5qs_fib84rbMU1pbAPY6YkO8WkC8GOQ/view?usp=sharing) | Trained on CelebA 64 × 64 |

# 2. Sampling
## 1) `"normal"` mode
```bash
# e.g.,
python3 ../sample.py\
    --mode="normal"\
    --model_params="....pth
    --save_path="samples/normal/0.jpg"\
    --img_size=64\
    --batch_size=100\
```
- <img src="https://github.com/KimRass/DDPM/assets/105417680/8d01e6d4-987d-4b0e-a45b-5ad1b155d448" width="450">
- <img src="https://github.com/KimRass/DDPM/assets/105417680/a7632da1-33cf-4413-ac77-e54bd643ddaa" width="700">
## 2) `"denoising_process"` mode
```bash
# e.g.,
python3 ../sample.py\
    --mode="denoising_process"\
    --model_params="....pth
    --save_path="samples/denoising_process/0.gif"\
    --img_size=64\
    --batch_size=100\
```
- <img src="https://github.com/KimRass/DDPM/assets/67457712/c7ec68bb-deba-45b5-b420-a068f65df9b6" width="210">
## 3) `"interpolation"` mode
```bash
# e.g.,
python3 ../sample.py\
    --mode="interpolation"\
    --model_params="....pth
    --save_path="samples/interpolation/0.jpg"\
    --img_size=64
    --data_dir="/Users/jongbeomkim/Documents/datasets/"\
    --image_idx1=50\
    --image_idx2=100\
```
- `interpolate_at=500`
    - <img src="https://github.com/KimRass/DDPM/assets/105417680/444c4c27-774c-4ec4-b07f-f2cbf7012433" width="700">
    - <img src="https://github.com/KimRass/DDPM/assets/105417680/ed7549de-73e1-4ea1-babe-0f1288584d5f" width="700">
    - <img src="https://github.com/KimRass/DDPM/assets/105417680/32c623d9-8e16-4913-a279-c48f75c05ffd" width="700">
    - <img src="https://github.com/KimRass/DDPM/assets/105417680/b7cb4e24-854d-4d47-8087-b1b4acf7f58b" width="700">
    - <img src="https://github.com/KimRass/DDPM/assets/105417680/88d32ee4-3155-4009-9796-e3c00fda9bc1" width="700">
## 4) `"coarse_to_fine"` mode
- Please refer to "Figure 9" in the paper for the meaning of each row and column.
```bash
# e.g.,
python3 ../sample.py\
    --mode="coarse_to_fine"\
    --model_params="....pth
    --save_path="samples/coarse_to_fine/0.jpg"\
    --img_size=64
    --data_dir="/Users/jongbeomkim/Documents/datasets/"\
    --image_idx1=50\
    --image_idx2=100\
```
- <img src="https://github.com/KimRass/DDPM/assets/105417680/6dcf9ba6-5988-44ed-92b2-a63d9db09719" width="700">

# 3. Evaluation
```bash
# e.g.,
python3 eval.py
    --ckpt_path=".....pth"\
    --real_data_dir="../img_align_celeba/"\
    --gen_data_dir="../ddpm_eval_images/"\
    --batch_size=32\
    --n_eval_imgs=28000\
    --n_cpus=4\ # Optional
    --padding=1\ # Optional
    --n_cells=100 # Optional
```

# 4. Theorectical Background
## 1) Forward (Diffusion) Process
$$q(x_{t} \vert x_{t - 1}) = \mathcal{N}(x_{t}; \sqrt{1 - \beta_{t}}x_{t - 1}, \beta_{t}I)$$
$$q(x_{t} \vert x_{0}) = \mathcal{N}(x_{t}; \sqrt{\bar{\alpha}_{t}}x_{0}, (1 - \bar{\alpha}_{t})I)$$
- Timestep이 매우 커질 때 이미지가 Normal gaussian distribution을 따르는 이유는?
    $$\prod_{s=1}^{t}{\alpha_{s}}$$
    - 1보다 작은 많은 수들을 서로 곱할 경우 0에 수렴합니다.
## 2) Backward (Denoising) Process
$$\mu_{\theta}(x_{t}, t) = \frac{1}{\sqrt{\alpha_{t}}}\Big(x_{t} - \frac{\beta_{t}}{\sqrt{1 - \bar{\alpha_{t}}}}\epsilon_{\theta}(x_{t}, t)\Big)$$
## 3) FID (Frechet Inception Distance)
$$\text{FID} = \lVert\mu_{X} - \mu_{Y}\rVert^{2}_{2} +Tr\big(\Sigma_{x} + \Sigma_{Y} - 2\sqrt{\Sigma_{X}\Sigma_{Y}}\big)$$
<!-- - Forward (diffusion) process
    - We define the forward diffusion process which adds Gaussian noise at each time step $t$, according to a known variance schedule $0 < \beta_{1} < \beta_{2} < \ldots < \beta_{T} < 1$ as
    $$q(x_{t} \vert x_{t - 1}) = \mathcal{N}(x_{t}; \sqrt{1 - \beta_{t}}x_{t - 1}, \beta_{t}I)$$
    - Each new (slightly noisier) image at time step $t$ is drawn from a conditional Gaussian distribution with $\mu_{t} = \sqrt{1 - \beta_{t}}x_{t - 1}$ and $\sigma_{t}^{2} = \beta_{t}$​, which we can do by sampling $\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$ and then setting $x_{t} = \sqrt{1 - \beta_{t}}x_{t - 1} + \sqrt{\beta_{t}}\epsilon$.
    - We can sample $x_{t}$​ at any arbitrary noise level conditioned on $x_{0}$​ (since sums of Gaussians is also Gaussian). This is very convenient: we don't need to apply q repeatedly in order to sample $x_{t}​$. We have that
    $$q(x_{1:T} | x_{0}) = \prod_{t = 1}^{T}q(x_{t} \vert x_{t - 1})$$
    $$q(x_{t} \vert x_{0}) = \mathcal{N}(x_{t}; \sqrt{\bar{\alpha}_{t}}x_{0}, (1 - \bar{\alpha}_{t})I)$$
    - where $\alpha_{t} = 1 - \beta_{t}$, $\bar{\alpha_{t}} = \prod_{s=1}^{t}{\alpha_{s}}$
    - This means we can sample Gaussian noise and scale it appropriatly and add it to $x_{0}$ to get $x_{t}$​ directly. Note that the $\bar{\alpha}_{t}$​ are functions of the known βt​ variance schedule and thus are also known and can be precomputed.
    - The mean can be computed as follows:
    $$\mu_{\theta}(x_{t}, t) = \frac{1}{\sqrt{\alpha_{t}}}\Big(x_{t} - \frac{\beta_{t}}{\sqrt{1 - \bar{\alpha_{t}}}}\epsilon_{\theta}(x_{t}, t)\Big)$$
- Backward (denoising) process
    - If we knew the conditional distribution $p(x_{t − 1}​ ∣ x_{t}​)$, then we could run the process in reverse: by sampling some random Gaussian noise $x_{T}$​, and then gradually "denoise" it so that we end up with a sample from the real distribution $x_{0}$​.
    - We're going to leverage a neural network to approximate (learn) this conditional probability distribution, let's call it $p_{\theta}(x_{t − 1} ∣ x_{t})$, with $\theta$ being the parameters of the neural network, updated by gradient descent.
    - We can parametrize the process as
    $$p_{\theta}(x_{t - 1} | x_{t}) = \mathcal{N}(x_{t - 1}; \mu_{\theta}(x_{t}, t), \Sigma(x_{t}, t)$$
    - where the mean and variance are also conditioned on the noise level $t$.
    - Hence, our neural network needs to learn/represent the mean and variance. However, the DDPM authors decided to keep the variance fixed, and let the neural network only learn (represent) the mean $\mu_{\theta}​$ of this conditional probability distribution. -->
<!-- ## 4) Kullback–Leibler Divergence (KL Divergence)
- Also called 'relative entropy' and 'I-divergence'.
$$D_{KL}(P || Q)$$
- A measure of how one probability distribution $P$ is different from a second, reference probability distribution $Q$.
- For discrete probability distributions $P$ and $Q$ defined on the same sample space, $\mathcal{X}$, the relative entropy from $Q$ to $P$ is defined to be
$$D_{KL}(P || Q) = - \sum_{x \in \mathcal{X}}P(x)\log\bigg(\frac{Q(x)}{P(x)}\bigg)$$
- For distributions $P$ and $Q$ of a continuous random variable, relative entropy is defined to be the integral:
$$D_{KL}(P || Q) = - \int_{-\infty}^{\infty} p(x)\log\bigg(\frac{q(x)}{p(x)}\bigg)dx$$
- where $p$ and $q$ denote the probability densities of $P$ and $Q$.

# 5. References
- https://huggingface.co/blog/annotated-diffusion
- https://medium.com/mlearning-ai/enerating-images-with-ddpms-a-pytorch-implementation-cef5a2ba8cb1
- https://lilianweng.github.io/posts/2021-07-11-diffusion-models/
- https://en.wikipedia.org/wiki/Kullback%E2%80%93Leibler_divergence
- https://junia3.github.io/blog/DDPMproof
- https://wandb.ai/capecape/train_sd/reports/How-To-Train-a-Conditional-Diffusion-Model-From-Scratch--VmlldzoyNzIzNTQ1
- https://keras.io/examples/generative/ddim/
