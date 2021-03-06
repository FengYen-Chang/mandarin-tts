import torch
import torch.nn as nn
import torch.nn.functional as F

from transformer.Models import Encoder, Decoder
#from transformer.Layers import PostNet
from unet import UNet
from modules import VarianceAdaptor
from utils import get_mask_from_lengths
import hparams as hp
from ipdb import set_trace
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


# OpenVINO
from openvino.inference_engine import IECore
import numpy as np

class FastSpeech2(nn.Module):
    """ FastSpeech2 """

    def __init__(self,  py_vocab_size,hz_vocab_size=None, use_postnet=True):
        super(FastSpeech2, self).__init__()

        self.encoder = Encoder(py_vocab_size, hz_vocab_size = hz_vocab_size)
        self.variance_adaptor = VarianceAdaptor()

        self.decoder = Decoder()
        self.mel_linear = nn.Linear(hp.decoder_hidden, hp.n_mel_channels)

        self.use_postnet = use_postnet
        if self.use_postnet:
            self.postnet = UNet(scale=8)

    def forward(self, src_seq, src_len, real_len=None, hz_seq = None,mel_len=None, d_target=None,  max_src_len=None, max_mel_len=None, d_control=1.0, p_control=1.0, e_control=1.0):
        src_mask = get_mask_from_lengths(src_len, max_src_len)
        mel_mask = get_mask_from_lengths(
            mel_len, max_mel_len) if mel_len is not None else None

        encoder_output = self.encoder(src_seq, src_mask, hz_seq)

        # Export the encoder graph to onnx model

        opset_version = 11
        encoder_input = (src_seq, src_mask,hz_seq)
        torch.onnx.export(self.encoder, encoder_input, "./onnx/encoder.onnx",
            opset_version=opset_version,
            do_constant_folding=True,
            input_names=["src_seq", "src_mask", "hz_seq"],
            output_names=["encoder_output"])

        if d_target is not None:
            variance_adaptor_output, d_prediction,   _, _, ori_len = self.variance_adaptor(
                encoder_output, src_mask, mel_mask, d_target,   max_mel_len, d_control, p_control, e_controli, real_len=real_len)
        else:
            variance_adaptor_output, d_prediction,   mel_len, mel_mask, ori_len = self.variance_adaptor(
                encoder_output, src_mask, mel_mask, d_target,   max_mel_len, d_control, p_control, e_control, real_len=real_len)

        decoder_output = self.decoder(variance_adaptor_output, mel_mask)

        # Export the decoder graph to onnx model

        opset_version = 11
        decoder_input = (variance_adaptor_output, mel_mask)
        torch.onnx.export(self.decoder, decoder_input, "./onnx/decoder.onnx",
            opset_version=opset_version,
            do_constant_folding=True,
            input_names=["variance_adaptor_output", "mel_mask"],
            output_names=["decoder_output"])
        decoder_output = decoder_output[:, :ori_len, :]

        mel_output = self.mel_linear(decoder_output)

        opset_version = 11
        mel_input = (decoder_output)
        torch.onnx.export(self.mel_linear, mel_input, "./onnx/mel.onnx",
            opset_version=opset_version,
            do_constant_folding=True,
            input_names=["decoder_output"],
            output_names=["mel_output"])

        if self.use_postnet:
            unet_out = self.postnet(torch.unsqueeze(mel_output,1))
            mel_output_postnet = unet_out[:,0,:,:]+ mel_output
        else:
            mel_output_postnet = mel_output

        return mel_output, mel_output_postnet, d_prediction,  src_mask, mel_mask, mel_len


if __name__ == "__main__":
    # Test
    model = FastSpeech2(use_postnet=False)
    print(model)
    print(sum(param.numel() for param in model.parameters()))
