# 'DDPM' (Ho et al., 2020) implementation from scratch in PyTorch
- [Denoising Diffusion Probabilistic Models](https://github.com/KimRass/DDPM/blob/main/papers/denoising_diffusion_probabilistic_models.pdf)
<!-- - We define the forward diffusion process $q(x_{t} \vert x_{t - 1})$ which adds Gaussian noise at each time step $t$, according to a known variance schedule $0 < \beta_{1} < \beta_{2} < \ldots < \beta_{T} < 1$ as
$$q(x_{t} \vert x_{t - 1}) = \mathcal{N}(x_{t}; \sqrt{1 - \beta_{t}}x_{t - 1}, \beta_{t}I)$$
- Recall that a normal distribution (also called Gaussian distribution) is defined by 2 parameters: a mean $\mu$ and a variance $\sigma^{2} \ge 0$. Basically, each new (slightly noisier) image at time step $t$ is drawn from a conditional Gaussian distribution with $\mu_{t} = \sqrt{1 - \beta_{t}}x_{t - 1}$ and $\sigma_{t}^{2} = \beta_{t}$​, which we can do by sampling $\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$ and then setting $x_{t} = \sqrt{1 - \beta_{t}}x_{t - 1} + \sqrt{\beta_{t}}\epsilon$.
- $T$​ is pure Gaussian noise if we set the schedule appropriately. -->
<!-- - If we knew the conditional distribution �(��−1∣��)p(xt−1​∣xt​), then we could run the process in reverse: by sampling some random Gaussian noise ��xT​, and then gradually "denoise" it so that we end up with a sample from the real distribution �0x0​.
- we're going to leverage a neural network to approximate (learn) this conditional probability distribution, let's call it ��(��−1∣��)pθ​(xt−1​∣xt​), with �θ being the parameters of the neural network, updated by gradient descent.
- If we assume this reverse process is Gaussian as well, then recall that any Gaussian distribution is defined by 2 parameters: 
- a mean parametrized by ��μθ​; 
- a variance parametrized by Σ�Σθ​; 
- so we can parametrize the process as��(��−1∣��)=�(��−1;��(��,�),Σ�(��,�))pθ​(xt−1​∣xt​)=N(xt−1​;μθ​(xt​,t),Σθ​(xt​,t))where the mean and variance are also conditioned on the noise level �t. 
- Hence, our neural network needs to learn/represent the mean and variance. However, the DDPM authors decided to keep the variance fixed, and let the neural network only learn (represent) the mean ��μθ​ of this conditional probability distribution. 
- We can sample ��xt​ at any arbitrary noise level conditioned on �0x0​ (since sums of Gaussians is also Gaussian). This is very convenient: we don't need to apply �q repeatedly in order to sample ��xt​. We have that�(��∣�0)=�(��;�ˉ��0,(1−�ˉ�)�)q(xt​∣x0​)=N(xt​;αˉt​​x0​,(1−αˉt​)I) 
- with ��=1−��αt​=1−βt​ and �ˉ�=Π�=1���αˉt​=Πs=1t​αs​. -->
<!-- - This means we can sample Gaussian noise and scale it appropriatly and add it to �0x0​ to get ��xt​ directly. Note that the �ˉ�αˉt​ are functions of the known ��βt​ variance schedule and thus are also known and can be precomputed.
- one can (after some math, for which we refer the reader to this excellent blog post) instead reparametrize the mean to make the neural network learn (predict) the added noise (via a network ��(��,�)ϵθ​(xt​,t)) for noise level �t in the KL terms which constitute the losses. This means that our neural network becomes a noise predictor, rather than a (direct) mean predictor. The mean can be computed as follows: 
��(��,�)=1��(��−��1−�ˉ���(��,�))μθ​(xt​,t)=αt​​1​(xt​−1−αˉt​​βt​​ϵθ​(xt​,t)) 
- The final objective function ��Lt​ then looks as follows (for a random time step �t given �∼�(0,�)ϵ∼N(0,I) ): 
∥�−��(��,�)∥2=∥�−��(�ˉ��0+(1−�ˉ�)�,�)∥2.∥ϵ−ϵθ​(xt​,t)∥2=∥ϵ−ϵθ​(αˉt​​x0​+(1−αˉt​)​ϵ,t)∥2. 
- $x_{0}$​ is the initial (real, uncorrupted) image, and we see the direct noise level $t$ sample given by the fixed forward process. $\epsilon$ is the pure noise sampled at time step $t$, and $\epsilon_{\theta}(x_{t}, t)$ is our neural network. The neural network is optimized using a simple mean squared error (MSE) between the true and the predicted Gaussian noise.
- we take a random sample �0x0​ from the real unknown and possibily complex data distribution �(�0)q(x0​) 
- we sample a noise level �t uniformally between 11 and �T (i.e., a random time step) 
- we sample some noise from a Gaussian distribution and corrupt the input by this noise at level �t (using the nice property defined above) 
- the neural network is trained to predict this noise based on the corrupted image ��xt​ (i.e. noise applied on �0x0​ based on known schedule ��βt​)
- The neural network needs to take in a noised image at a particular time step and return the predicted noise. Note that the predicted noise is a tensor that has the same size/resolution as the input image. So technically, the network takes in and outputs tensors of the same shape.
- This makes the neural network "know" at which particular time step (noise level) it is operating, for every image in a batch. -->

<!-- - The joint distribution $p_{\theta}(x_{0:T})$ is called the reverse process, and it is defined as a Markov chain with learned Gaussian transitions starting at $p(x_{T}) = \mathcal{N}(x_{T};0,I)$ (Comment: The variable $x_{T}$ follows normal distribution with mean $0$ and variance $I$.):
$$p_{\theta}(x_{t - 1} \vert x_{t}) = \mathcal{N}(x_{t - 1}; \mu_{\theta}(x_{t}, t), \Sigma_{\theta}(x_{t}, t))$$
<!-- $$p_{\theta}(x_{0:T}) = p_{\theta}(x_{T})\prod^{T}_{t = 1}p_{\theta}(x_{t - 1} \vert x_{t})$$ -->
<!-- - (Comment: On reverse process, $x_{t - 1}$ follows normal distribution with mean $\mu_{\theta}$ and variance $\Sigma_{\theta}$ given $x_{t}$.)
- What distinguishes diffusion models from other types of latent variable models is that the approximate posterior $q(x_{1:T} | x_{0})$, called the forward process or diffusion process, is fixed to a Markov chain that gradually adds Gaussian noise to the data according to a variance schedule $β_{1}, \ldots, β_{T}$ ($0 < \beta_{1} < \beta_{2} < \ldots < \beta_{T} < 1$):
$$q(x_{t} \vert x_{t - 1}) = \mathcal{N}(x_{t}; \sqrt{1 - \beta_{t}}x_{t - 1}, \beta_{t}I)$$
$$q(x_{1:T} | x_{0}) = \prod^{T}_{t = 1}q(x_{t} \vert x_{t - 1})$$
$$q(x_{t} \vert x_{0}) = \mathcal{N}(x_{t}; \sqrt{\bar{\alpha}_{t}}x_{0}, (1 - \bar{\alpha}_{t})I)$$
- where $\alpha_{t} = 1 - \beta_{t}$, $\bar{\alpha_{t}} = \prod^{t}_{s=1}{\alpha_{s}}$
- This means we can sample Gaussian noise and scale it appropriatly and add it to $x_{0}$ to get $x_{t}$ directly.
- The mean can be computed as follows:
$$\mu_{\theta}(x_{t}, t) = \frac{1}{\sqrt{\alpha_{t}}}\Big(x_{t} - \frac{\beta_{t}}{\sqrt{1 - \bar{\alpha_{t}}}}\epsilon_{\theta}(x_{t}, t)\Big)$$
- We take a random sample $x_{0}$ from the real unknown and possibily complex data distribution $q(x_{0})$.
- We sample a noise level $t$ uniformally between $1$ and $T$ (i.e., a random time step).
- We sample some noise from a Gaussian distribution and corrupt the input by this noise at level at $t$.
- The neural network is trained to predict this noise based on the corrupted image $x_{t}$ (i.e. noise applied on $x_{0}$ based on known schedule $\beta_{t}$).
$$\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$$
$$p_{\theta}(x_{0:T}) = p_{\theta}(x_{T})\prod^{T}_{t = 1}p_{\theta}(x_{t - 1} \vert x_{t})$$ -->
<!-- ## Kullback–Leibler Divergence (KL Divergence)
- Also called 'relative entropy' and 'I-divergence'.
$$D_{KL}(P || Q)$$
- A measure of how one probability distribution $P$ is different from a second, reference probability distribution $Q$.
- For discrete probability distributions $P$ and $Q$ defined on the same sample space, $\mathcal{X}$, the relative entropy from $Q$ to $P$ is defined to be
$$D_{KL}(P || Q) = - \sum_{x \in \mathcal{X}}P(x)\log\bigg(\frac{Q(x)}{P(x)}\bigg)$$
- For distributions $P$ and $Q$ of a continuous random variable, relative entropy is defined to be the integral:
$$D_{KL}(P || Q) = - \int_{-\infty}^{\infty} p(x)\log\bigg(\frac{q(x)}{p(x)}\bigg)dx$$
- where $p$ and $q$ denote the probability densities of $P$ and $Q$. -->
## Pre-trained Models
- [ddpm_celeba.pth](https://drive.google.com/file/d/10nYTU1NNv3GghPwb8Mgp29Seni6iTI1a/view?usp=sharing)
    - Trained on CelebA dataset for 29epochs
    ```python
    IMG_SIZE = 32
    BATCH_SIZE = 256
    LR = 0.0001
    ```
<!-- ```python
IMG_SIZE = 64
BATCH_SIZE = 64
LR = 0.00005
``` -->
## Generated Image Sample
- <img src="https://github.com/KimRass/DDPM/assets/67457712/c7ec68bb-deba-45b5-b420-a068f65df9b6" width="300">
- How to generate images
    ```bash
    # e.g.,
    python3 generate_images.py\
        --ckpt_path="../ddpm_celeba.pth"\
        --batch_size=36\
        --save_path="generated_images/4.jpg"
    ```
## References
- https://huggingface.co/blog/annotated-diffusion
- https://medium.com/mlearning-ai/enerating-images-with-ddpms-a-pytorch-implementation-cef5a2ba8cb1
- https://lilianweng.github.io/posts/2021-07-11-diffusion-models/
- https://en.wikipedia.org/wiki/Kullback%E2%80%93Leibler_divergence
