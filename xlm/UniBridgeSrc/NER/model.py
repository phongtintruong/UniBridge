import os
from typing import Optional

import torch
import torch.nn as nn
from torch import Tensor
from transformers import AdapterConfig, BertAdapterModel, PreTrainedModel, XLMRobertaAdapterModel

from .configuration import NERAdapterConfig


class NERAdapterPreTrainedModel(PreTrainedModel):
    config_class = NERAdapterConfig
    base_model_prefix = "ner_adapter"
    supports_gradient_checkpointing = True

    def _init_weights(self, module):
        """Initialize the weights"""
        if isinstance(module, nn.Linear):
            module.weight.data.normal_(mean=0.0, std=self.config.initializer_range)
            if module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=self.config.initializer_range)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()
        elif isinstance(module, nn.LayerNorm):
            module.bias.data.zero_()
            module.weight.data.fill_(1.0)

    @property
    def dummy_inputs(self):
        pad_token = self.config.pad_token_id
        input_ids = torch.tensor([[0, 6, 10, 4, 2], [0, 8, 12, 2, pad_token]], device=self.device)
        dummy_inputs = {
            "attention_mask": input_ids.ne(pad_token),
            "input_ids": input_ids,
            "decoder_input_ids": input_ids,
        }
        return dummy_inputs


class NERAdapterModel(NERAdapterPreTrainedModel):
    _keys_to_ignore_on_load_unexpected = [r"pooler"]
    _keys_to_ignore_on_load_missing = [r"position_ids"]

    def __init__(self, config: NERAdapterConfig):
        super().__init__(config)
        self.num_labels = config.num_labels

        if config.model in ["mbert", "mbert_cased"]:
            basemodel_class = BertAdapterModel
        elif config.model == "xlm-r":
            basemodel_class = XLMRobertaAdapterModel
        else:
            assert f"parameter `model` for NERAdapterModel must be either ['mbert', 'xlm-r'], {config.model} does not belong to that."

        if config.pretrained_ck == "":
            self.model = basemodel_class(config)  # , add_pooling_layer=False)
        else:
            self.model = basemodel_class.from_pretrained(
                config.pretrained_ck
            )  # , add_pooling_layer=False)#, output_hidden_states=True)

        adapter_mode = 0
        if config.lang_adapter_ckpt == "":
            adapter_mode = 1
        else:
            # load language adapter
            self.model.load_adapter(config.lang_adapter_ckpt, load_as=config.lang, leave_out=[11])
            adapter_mode = 2

        if config.task_adapter_ckpt == "":
            # add task adapter
            task_config = AdapterConfig.load("pfeiffer", non_linearity="gelu", reduction_factor=16, leave_out=[11])
            self.model.add_adapter(f"{config.lang}_ner", config=task_config)
            self.model.add_tagging_head(
                f"{config.lang}_ner", num_labels=config.num_labels
            )  # , id2label=config.id2label)
        else:
            self.model.load_adapter(config.task_adapter_ckpt, load_as=f"{config.lang}_ner", leave_out=[11])

        if adapter_mode == 1:
            self.model.set_active_adapters([f"{config.lang}_ner"])
        elif adapter_mode == 2:
            self.model.set_active_adapters([config.lang, f"{config.lang}_ner"])
        else:
            assert f"Unknown adapter mode of {adapter_mode}"
        self.model.train_adapter([f"{config.lang}_ner"])

        self.config = config

        # self.post_init()

    def save_pretrained(self, path):
        if not os.path.exists(f"{path}/{self.config.lang}"):
            os.makedirs(f"{path}/{self.config.lang}")
        # save this wrap model config
        self.model.config.save_pretrained(f"{path}/{self.config.lang}")
        self.model.save_adapter(f"{path}/{self.config.lang}", f"{self.config.lang}_ner", with_head=True)

    def forward(
        self,
        input_ids: Optional[torch.LongTensor] = None,
        attention_mask: Optional[torch.FloatTensor] = None,
        token_type_ids: Optional[torch.LongTensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        head_mask: Optional[torch.FloatTensor] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        labels: Optional[torch.LongTensor] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
    ) -> Tensor:
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        outputs = self.model(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        return outputs["logits"]
