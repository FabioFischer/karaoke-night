# Vendored from SOME (https://github.com/openvpi/SOME),
# modules/model/Gmidi_conform.py at commit
# e523eef70777c1370199363de03eb37d66dce76e. Copyright (c) 2023 Team OpenVPI.
# MIT License, see LICENSE in studio/some. Changes: imports adapted to
# studio.some; the training loss (midi_loss, get_loss) removed.

import torch
import torch.nn as nn
import torch.nn.functional as F
from studio.some.modules.conform.Gconform import Gmidi_conform



class midi_conforms(nn.Module):
    def __init__(self, config):
        super().__init__()

        cfg = config['midi_extractor_args']
        cfg.update({'indim': config['units_dim'], 'outdim': config['midi_num_bins']})
        self.model = Gmidi_conform(**cfg)

    def forward(self, x, f0, mask=None,softmax=False,sig=False):

        midi,bound=self.model(x, f0, mask)
        if  sig:
            midi = torch.sigmoid(midi)

        if softmax:
            midi=F.softmax(midi,dim=2)


        return midi,bound
