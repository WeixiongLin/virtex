import glob
import os
from typing import Callable, List, Tuple

import albumentations as alb
import dataflow as df
import numpy as np
from PIL import Image
import tokenizers as tkz
from torch.utils.data import Dataset, IterableDataset

from viswsl.data.dataflows import (
    ReadDatapointsFromLmdb,
    RandomHorizontalFlip,
    TokenizeCaption,
)
from viswsl.data.structures import CaptioningInstance, CaptioningBatch


class CaptioningPretextDataset(IterableDataset):
    def __init__(
        self,
        lmdb_path: str,
        tokenizer: tkz.implementations.BaseTokenizer,
        image_transform: Callable = alb.Compose(
            [
                alb.SmallestMaxSize(max_size=256),
                alb.RandomCrop(224, 224),
                alb.ToFloat(max_value=255.0),
                alb.Normalize(
                    mean=(0.485, 0.456, 0.406),
                    std=(0.229, 0.224, 0.225),
                    max_pixel_value=1.0,
                ),
            ]
        ),
        random_horizontal_flip: bool = True,
        max_caption_length: int = 30,
        shuffle: bool = False,
    ):
        self.image_transform = image_transform
        # keys: {"image_id", "image", "caption"}
        self._pipeline = ReadDatapointsFromLmdb(lmdb_path, shuffle=shuffle)

        # Random horizontal flip is kept separate from other data augmentation
        # transforms because we need to change the caption if image is flipped.
        if random_horizontal_flip:
            self._pipeline = RandomHorizontalFlip(self._pipeline)

        # keys added: {"caption_tokens"}
        self._pipeline = TokenizeCaption(
            self._pipeline,
            tokenizer,
            input_key="caption",
            output_key="caption_tokens",
        )
        self.max_caption_length = max_caption_length
        self.padding_idx = tokenizer.token_to_id("[UNK]")

    def __iter__(self):
        self._pipeline.reset_state()

        for datapoint in self._pipeline:
            # Transform and convert image from HWC to CHW format.
            image = self.image_transform(image=datapoint["image"])["image"]
            image = np.transpose(image, (2, 0, 1))

            # Trim captions up to maximum length.
            caption_tokens = datapoint["caption_tokens"]
            caption_tokens = caption_tokens[: self.max_caption_length]

            yield CaptioningInstance(datapoint["image_id"], image, caption_tokens)

    def collate_fn(self, instances: List[CaptioningInstance]) -> CaptioningBatch:
        return CaptioningBatch(instances, padding_value=self.padding_idx)


class CocoCaptionsEvalDataset(Dataset):
    def __init__(
        self,
        image_dir: str,
        image_transform: Callable = alb.Compose(
            [
                alb.Resize(224, 224),
                alb.ToFloat(max_value=255.0),
                alb.Normalize(
                    mean=(0.485, 0.456, 0.406),
                    std=(0.229, 0.224, 0.225),
                    max_pixel_value=1.0,
                ),
            ]
        ),
    ):
        self.image_transform = image_transform
        image_filenames = glob.glob(os.path.join(image_dir, "*.jpg"))

        # Make a tuple of image id and its filename, get image_id from its
        # filename (assuming directory has images with names in COCO 2017 format).
        self.id_filename: Tuple[int, str] = [
            (int(os.path.basename(name)[:-4]), name) for name in image_filenames
        ]

    def __len__(self):
        return len(self.id_filename)

    def __getitem__(self, idx: int):
        image_id, filename = self.id_filename[idx]

        # Open image from path and apply transformation, convert to CHW format.
        image = np.array(Image.open(filename).convert("RGB"))
        image = self.image_transform(image=image)["image"]
        image = np.transpose(image, (2, 0, 1))

        return CaptioningInstance(image_id, image)

    def collate_fn(self, instances: List[CaptioningInstance]) -> CaptioningBatch:
        return CaptioningBatch(instances)
