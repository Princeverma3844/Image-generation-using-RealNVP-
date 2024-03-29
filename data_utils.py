"""Utility functions for real NVP.
"""

import torch
import torch.nn.functional as F
import torch.distributions as distributions
import torch.utils.data as data

import torchvision.datasets as datasets
import torchvision.transforms as transforms
import numpy as np

import pandas as pd
import os
from torch.utils.data import Dataset
from PIL import Image

def get_csv(data_folder, csv_path):
    paths = os.listdir(data_folder)
    for i in range(len(paths)):
        paths[i] = data_folder + "/" + paths[i]
    paths = sorted(paths)
    csvs = pd.read_csv(csv_path).sort_values(by=['image_id'])
    csvs['image_id'] = paths
    csvs.to_csv("paths.csv")
    return csvs

class CelebA(Dataset):
    def __init__(self, csv_paths, split="train", transform=transforms.ToTensor()):
        self.file_paths = pd.read_csv(csv_paths)

        if split == "train":
            self.file_paths = self.file_paths.loc[self.file_paths['partition'] == 0]
        elif split == "val":
            self.file_paths = self.file_paths.loc[self.file_paths['partition'] == 1]
        elif split == "test":
            self.file_paths = self.file_paths.loc[self.file_paths['partition'] == 2]

        self.transform = transform

    def __len__(self):
        return len(self.file_paths)

    def __getitem__(self, idx):
        img = Image.open(self.file_paths.iloc[idx]['image_id'])

        if self.transform is not None:
            img = self.transform(img)

        return img


class DataInfo():
    def __init__(self, name, channel, size):
        """Instantiates a DataInfo.

        Args:
            name: name of dataset.
            channel: number of image channels.
            size: height and width of an image.
        """
        self.name = name
        self.channel = channel
        self.size = size

def load(dataset):
    """Load dataset.

    Args:
        dataset: name of dataset.
    Returns:
        a torch dataset and its associated information.
    """
    if dataset == 'cifar10':    # 3 x 32 x 32
        data_info = DataInfo(dataset, 3, 32)
        transform = transforms.Compose(
            [transforms.RandomHorizontalFlip(p=0.5), 
             transforms.ToTensor()])
        train_set = datasets.CIFAR10('./data', 
            train=True, download=False, transform=transform)
        [train_split, val_split] = data.random_split(train_set, [46000, 4000])

    elif dataset == 'celeba':   # 3 x 218 x 178
        data_info = DataInfo(dataset, 3, 64)
        def CelebACrop(images):
            return transforms.functional.crop(images, 40, 15, 148, 148)
        transform = transforms.Compose([CelebACrop, 
                                        transforms.Resize(64), 
                                        transforms.RandomHorizontalFlip(p=0.5), 
                                        transforms.ToTensor()])
        ans = get_csv(data_folder="./img_align_celeba/img_align_celeba", csv_path="./celeb_A_dataset.csv")
        train_split = CelebA(csv_paths="paths.csv", split="train", transform=transform)
        val_split = CelebA(csv_paths="paths.csv", split="val", transform=transform)

    elif dataset == 'imnet32':
        data_info = DataInfo(dataset, 3, 32)
        transform = transforms.Compose(
            [transforms.RandomHorizontalFlip(p=0.5),
            transforms.ToTensor()])
        train_set = datasets.ImageFolder('../../data/ImageNet32/train', 
            transform=transform)
        [train_split, val_split] = data.random_split(train_set, [1250000, 31149])

    elif dataset == 'imnet64':
        data_info = DataInfo(dataset, 3, 64)
        transform = transforms.Compose(
            [transforms.RandomHorizontalFlip(p=0.5),
            transforms.ToTensor()])
        train_set = datasets.ImageFolder('../../data/ImageNet64/train', 
            transform=transform)
        [train_split, val_split] = data.random_split(train_set, [1250000, 31149])

    return train_split, val_split, data_info

def logit_transform(x, constraint=0.9, reverse=False):
    '''Transforms data from [0, 1] into unbounded space.

    Restricts data into [0.05, 0.95].
    Calculates logit(alpha+(1-alpha)*x).

    Args:
        x: input tensor.
        constraint: data constraint before logit.
        reverse: True if transform data back to [0, 1].
    Returns:
        transformed tensor and log-determinant of Jacobian from the transform.
        (if reverse=True, no log-determinant is returned.)
    '''
    if reverse:
        x = 1. / (torch.exp(-x) + 1.)    # [0.05, 0.95]
        x *= 2.             # [0.1, 1.9]
        x -= 1.             # [-0.9, 0.9]
        x /= constraint     # [-1, 1]
        x += 1.             # [0, 2]
        x /= 2.             # [0, 1]
        return x, 0
    else:
        [B, C, H, W] = list(x.size())
        
        # dequantization
        noise = distributions.Uniform(0., 1.).sample((B, C, H, W))
        x = (x * 255. + noise) / 256.
        
        # restrict data
        x *= 2.             # [0, 2]
        x -= 1.             # [-1, 1]
        x *= constraint     # [-0.9, 0.9]
        x += 1.             # [0.1, 1.9]
        x /= 2.             # [0.05, 0.95]

        # logit data
        logit_x = torch.log(x) - torch.log(1. - x)

        # log-determinant of Jacobian from the transform
        pre_logit_scale = torch.tensor(
            np.log(constraint) - np.log(1. - constraint))
        log_diag_J = F.softplus(logit_x) + F.softplus(-logit_x) \
            - F.softplus(-pre_logit_scale)

        return logit_x, torch.sum(log_diag_J, dim=(1, 2, 3))
    