"""Training procedure for real NVP.
"""

import argparse

import torch, torchvision
import torch.distributions as distributions
import torch.optim as optim
import torchvision.utils as utils
import matplotlib.pyplot as plt
import numpy as np
import realnvp, data_utils

class Hyperparameters():
    def __init__(self, base_dim, res_blocks, bottleneck, 
        skip, weight_norm, coupling_bn, affine):
        """Instantiates a set of hyperparameters used for constructing layers.

        Args:
            base_dim: features in residual blocks of first few layers.
            res_blocks: number of residual blocks to use.
            bottleneck: True if use bottleneck, False otherwise.
            skip: True if use skip architecture, False otherwise.
            weight_norm: True if apply weight normalization, False otherwise.
            coupling_bn: True if batchnorm coupling layer output, False otherwise.
            affine: True if use affine coupling, False if use additive coupling.
        """
        self.base_dim = base_dim
        self.res_blocks = res_blocks
        self.bottleneck = bottleneck
        self.skip = skip
        self.weight_norm = weight_norm
        self.coupling_bn = coupling_bn
        self.affine = affine

def main(args):
    # device = torch.device("cuda:0")

    device = torch.device("cuda")
    print(device)
    # model hyperparameters
    dataset = args.dataset
    batch_size = args.batch_size
    hps = Hyperparameters(
        base_dim = args.base_dim, 
        res_blocks = args.res_blocks, 
        bottleneck = args.bottleneck, 
        skip = args.skip, 
        weight_norm = args.weight_norm, 
        coupling_bn = args.coupling_bn, 
        affine = args.affine)
    scale_reg = 5e-5    # L2 regularization strength

    # optimization hyperparameters
    lr = args.lr
    momentum = args.momentum
    decay = args.decay

    # prefix for images and checkpoints
    filename = 'bs%d_' % batch_size \
             + 'normal_' \
             + 'bd%d_' % hps.base_dim \
             + 'rb%d_' % hps.res_blocks \
             + 'bn%d_' % hps.bottleneck \
             + 'sk%d_' % hps.skip \
             + 'wn%d_' % hps.weight_norm \
             + 'cb%d_' % hps.coupling_bn \
             + 'af%d' % hps.affine \

    # load dataset
    train_split, val_split, data_info = data_utils.load(dataset)
    train_loader = torch.utils.data.DataLoader(train_split,
        batch_size=batch_size, shuffle=True)
    val_loader = torch.utils.data.DataLoader(val_split,
        batch_size=batch_size, shuffle=False)

    prior = distributions.Normal(   # isotropic standard normal distribution
        torch.tensor(0.).to(device), torch.tensor(1.).to(device))
    flow = realnvp.RealNVP(datainfo=data_info, prior=prior, hps=hps).to(device)
    print(sum(p.numel() for p in flow.parameters()))
    optimizer = optim.Adamax(flow.parameters(), lr=lr, betas=(momentum, decay), eps=1e-7)
    
    epoch = 0
    running_loss = 0.
    running_log_ll = 0.
    optimal_log_ll = float('-inf')
    early_stop = 0

    image_size = data_info.channel * data_info.size**2    # full image dimension

    losses_train = []
    losses_val = []
    while epoch < args.max_epoch:
        epoch += 1
        print('Epoch %d:' % epoch)
        flow.train()
        for batch_idx, data in enumerate(train_loader, 1):
            optimizer.zero_grad()
            x= data
            # log-determinant of Jacobian from the logit transform
            x, log_det = data_utils.logit_transform(x)
            x = x.to(device)
            log_det = log_det.to(device)

            # log-likelihood of input minibatch
            log_ll_, weight_scale = flow(x)

            log_ll = (log_ll_ + log_det).mean()

            # add L2 regularization on scaling factors
            loss = -log_ll + scale_reg * weight_scale
            running_loss += loss.item()
            running_log_ll += log_ll.item()

            loss.backward()
            optimizer.step()

            if batch_idx % 1000 == 0:
                bit_per_dim = (-log_ll.item() + np.log(256.) * image_size) \
                    / (image_size * np.log(2.))
                print('[%d/%d]\tloss: %.3f\tlog-ll: %.3f\tbits/dim: %.3f' % \
                    (batch_idx*batch_size, len(train_loader.dataset), 
                        loss.item(), log_ll.item(), bit_per_dim))
        
        mean_loss = running_loss / batch_idx
        losses_train.append(mean_loss)

        mean_log_ll = running_log_ll / batch_idx
        mean_bit_per_dim = (-mean_log_ll + np.log(256.) * image_size) \
             / (image_size * np.log(2.))
        print('===> Average train loss: %.3f' % mean_loss)
        print('===> Average train log-likelihood: %.3f' % mean_log_ll)
        print('===> Average train bit_per_dim: %.3f' % mean_bit_per_dim)
        running_loss = 0.
        running_log_ll = 0.

        flow.eval()
        with torch.no_grad():
            for batch_idx, data in enumerate(val_loader, 1):
                x = data
                x, log_det = data_utils.logit_transform(x)
                x = x.to(device)
                log_det = log_det.to(device)

                # log-likelihood of input minibatch
                log_ll_, weight_scale = flow(x)

                log_ll = (log_ll_ + log_det).mean()
                # add L2 regularization on scaling factors
                loss = -log_ll + scale_reg * weight_scale
                running_loss += loss.item()
                running_log_ll += log_ll.item()

            mean_loss = running_loss / batch_idx
            mean_log_ll = running_log_ll / batch_idx
            mean_bit_per_dim = (-mean_log_ll + np.log(256.) * image_size) \
                / (image_size * np.log(2.))
            print('===> Average validation loss: %.3f' % mean_loss)
            print('===> Average validation log-likelihood: %.3f' % mean_log_ll)
            print('===> Average validation bits/dim: %.3f' % mean_bit_per_dim)

            losses_val.append(mean_loss)

            plt.plot(losses_train, label='Training Loss')
            plt.plot(losses_val, label='Validation Loss')
            plt.title('Loss curves')
            # plt.legend()
            plt.savefig('/csehome/verma.43/loss_curves.png')
            running_loss = 0.
            running_log_ll = 0.
            # torch.save(flow.state_dict(), 'trained_weights.pt')

            samples = flow.sample(args.sample_size)
            samples, _ = data_utils.logit_transform(samples, reverse=True)
            if(epoch <= 15 or epoch == 25 or epoch == 35 or epoch == 45 or epoch%10 == 0):
                utils.save_image(utils.make_grid(samples),
                    f'/csehome/verma.43/epoch{epoch}.png')

            torch.save(flow.state_dict(), '/csehome/verma.43/trained_weights.pt')

        if mean_log_ll > optimal_log_ll:
            early_stop = 0
            optimal_log_ll = mean_log_ll
            torch.save(flow.state_dict(), '/csehome/verma.43/trained_weights.pt')
        else:
            early_stop += 1
            if early_stop >= 100:
                break
        
        print('--> Early stopping %d/100 (BEST validation log-likelihood: %.3f)' \
            % (early_stop, optimal_log_ll))

    print('Training finished at epoch %d.' % epoch)

if __name__ == '__main__':
    parser = argparse.ArgumentParser('Real NVP PyTorch implementation')
    parser.add_argument('--dataset',
                        help='dataset to be modeled.',
                        type=str,
                        default='celeba')
    parser.add_argument('--batch_size',
                        help='number of images in a mini-batch.',
                        type=int,
                        default=16)
    parser.add_argument('--base_dim',
                        help='features in residual blocks of first few layers.',
                        type=int,
                        default=32)
    parser.add_argument('--res_blocks',
                        help='number of residual blocks per group.',
                        type=int,
                        default=6)
    parser.add_argument('--bottleneck',
                        help='whether to use bottleneck in residual blocks.',
                        type=int,
                        default=0)
    parser.add_argument('--skip',
                        help='whether to use skip connection in coupling layers.',
                        type=int,
                        default=1)
    parser.add_argument('--weight_norm',
                        help='whether to apply weight normalization.',
                        type=int,
                        default=1)
    parser.add_argument('--coupling_bn',
                        help='whether to apply batchnorm after coupling layers.',
                        type=int,
                        default=1)
    parser.add_argument('--affine',
                        help='whether to use affine coupling.',
                        type=int,
                        default=1)
    parser.add_argument('--max_epoch',
                        help='maximum number of training epoches.',
                        type=int,
                        default=300)
    parser.add_argument('--sample_size',
                        help='number of images to generate.',
                        type=int,
                        default=32)
    parser.add_argument('--lr',
                        help='initial learning rate.',
                        type=float,
                        default=1e-3)
    parser.add_argument('--momentum',
                        help='beta1 in Adam optimizer.',
                        type=float,
                        default=0.9)
    parser.add_argument('--decay',
                        help='beta2 in Adam optimizer.',
                        type=float,
                        default=0.999)
    args = parser.parse_args()
    main(args)


