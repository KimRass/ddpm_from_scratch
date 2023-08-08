import torch
import torch.nn.functional as F
import torchvision.transforms as T
from PIL import Image

import config

INIT_BETA = 0.0001
FIN_BETA = 0.02


# "We set the forward process variances to constants increasing linearly from $\beta_{1} = 10^{-4}$
# to $\beta_{T} = 0.02$.
def linear_beta_schedule(n_timesteps):
    return torch.linspace(INIT_BETA, FIN_BETA, n_timesteps)


n_timesteps = 300
betas = linear_beta_schedule(n_timesteps)
alphas = 1 - betas # $\alpha_{t} = 1 - \beta_{t}$
alpha_bars = torch.cumprod(alphas, dim=0) # $\bar{\alpha_{t}} = \prod^{t}_{s=1}{\alpha_{s}}$
alpha_bars_prev = F.pad(alpha_bars[:-1], pad=(1, 0), value=1.0)
# sqrt_recip_alphas = torch.sqrt(1 / alphas)
sqrt_recip_alphas = (1 / alphas) ** 0.5

# sqrt_one_minus_alpha_bars = (1 - alpha_bars) ** 0.5

posterior_variance = betas * (1. - alpha_bars_prev) / (1. - alpha_bars)
posterior_variance

image = Image.open("/Users/jongbeomkim/Documents/datasets/voc2012/VOCdevkit/VOC2012/JPEGImages/2007_001709.jpg")

# "We assume that image data consists of integers in $\{0, 1, \ldots, 255\}$ scaled linearly
# to $[-1, 1]$. This ensures that the neural network reverse process operates
# on consistently scaled inputs starting from the standard normal prior $p(x_{T})$."
IMG_SIZE = 128
transformer = T.Compose([
    T.Resize(IMG_SIZE),
    T.CenterCrop(IMG_SIZE),
    T.ToTensor(),
    T.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))
])
reverse_transformer = T.Compose([
     T.Lambda(lambda t: (t + 1) / 2),
     T.ToPILImage(),
])

init_x = transformer(image).unsqueeze(0)
init_x


def extract(a, t, shape):
    b = t.shape[0]
    out = torch.gather(a, dim=-1, index=t)
    out = out.reshape(b, *((1,) * (len(shape) - 1)))
    return out


if noise is None:
    noise = torch.randn_like(init_x) # $\epsilon \sim \mathcal{N}(\mathbf{0}, \mathbf{I})$

t = torch.tensor([40])
alpha_bar = extract(alpha_bars, t=t, shape=init_x.shape)
var = 1 - alpha_bars # $(1 - \bar{\alpha_{t}})\mathbf{I}$
(var ** 0.5) * noise

sqrt_alpha_bars = alpha_bars ** 0.5
