import torch
import torch.nn as nn
import copy
from vit_ID import TransReID, Block
from functools import partial
from torch.nn import functional as F
from vit_ID import resize_pos_embed

def TCSS(features, shift, b, t):
    # aggregate features at patch level
    features = features.view(b, features.size(1), t * features.size(2))
    token = features[:, 0:1]

    batchsize = features.size(0)
    dim = features.size(-1)
    
    # shift the patches with amount=shift
    features = torch.cat([features[:, shift:], features[:, 1:shift]], dim=1)
    
    # Patch Shuffling by 2 part
    try:
        features = features.view(batchsize, 2, -1, dim)
    except:
        features = torch.cat([features, features[:, -2:-1, :]], dim=1)
        features = features.view(batchsize, 2, -1, dim)
    
    features = torch.transpose(features, 1, 2).contiguous()
    features = features.view(batchsize, -1, dim)
    
    return features, token    

def weights_init_kaiming(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_out')
        nn.init.constant_(m.bias, 0.0)
    elif classname.find('Conv') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in')
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)
    elif classname.find('BatchNorm') != -1:
        if m.affine:
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0.0)

def weights_init_classifier(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.normal_(m.weight, std=0.001)
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)

class VID_Trans(nn.Module):
    def __init__(self, num_classes, camera_num, pretrainpath):
        super(VID_Trans, self).__init__()
        self.in_planes = 768
        self.num_classes = num_classes
        
        self.base = TransReID(
            img_size=[256, 128], patch_size=16, stride_size=[16, 16], embed_dim=768,
            depth=12, num_heads=12, mlp_ratio=4, qkv_bias=True,
            camera=camera_num, drop_path_rate=0.1, drop_rate=0.0, attn_drop_rate=0.0,
            norm_layer=partial(nn.LayerNorm, eps=1e-6), cam_lambda=3.0
        )
        
        state_dict = torch.load(pretrainpath, map_location='cpu')
        self.base.load_param(state_dict, load=True)
        
        # global stream
        block = self.base.blocks[-1]
        layer_norm = self.base.norm
        self.b1 = nn.Sequential(
            copy.deepcopy(block),
            copy.deepcopy(layer_norm)
        )
        
        self.bottleneck = nn.BatchNorm1d(self.in_planes)
        self.bottleneck.bias.requires_grad_(False)
        self.bottleneck.apply(weights_init_kaiming)
        self.classifier = nn.Linear(self.in_planes, self.num_classes, bias=False)
        self.classifier.apply(weights_init_classifier)
        #-----------------------------------------------
        # building local video stream
        dpr = [x.item() for x in torch.linspace(0, 0, 12)]  # stochastic depth decay rule
        
        self.block1 = Block(
            dim=3072, num_heads=12, mlp_ratio=4, qkv_bias=True, qk_scale=None,
            drop=0, attn_drop=0, drop_path=dpr[11], norm_layer=partial(nn.LayerNorm, eps=1e-6)
        )
        self.b2 = nn.Sequential(
            self.block1,
            nn.LayerNorm(3072)
        )
        self.bottleneck_1 = nn.BatchNorm1d(3072)
        self.bottleneck_1.bias.requires_grad_(False)
        self.bottleneck_1.apply(weights_init_kaiming)
        self.bottleneck_2 = nn.BatchNorm1d(3072)
        self.bottleneck_2.bias.requires_grad_(False)
        self.bottleneck_2.apply(weights_init_kaiming)
        self.bottleneck_3 = nn.BatchNorm1d(3072)
        self.bottleneck_3.bias.requires_grad_(False)
        self.bottleneck_3.apply(weights_init_kaiming)
        self.bottleneck_4 = nn.BatchNorm1d(3072)
        self.bottleneck_4.bias.requires_grad_(False)
        self.bottleneck_4.apply(weights_init_kaiming)

        self.classifier_1 = nn.Linear(3072, self.num_classes, bias=False)
        self.classifier_1.apply(weights_init_classifier)
        self.classifier_2 = nn.Linear(3072, self.num_classes, bias=False)
        self.classifier_2.apply(weights_init_classifier)
        self.classifier_3 = nn.Linear(3072, self.num_classes, bias=False)
        self.classifier_3.apply(weights_init_classifier)
        self.classifier_4 = nn.Linear(3072, self.num_classes, bias=False)
        self.classifier_4.apply(weights_init_classifier)
        #-------------------video attention-------------
        self.middle_dim = 256  # middle layer dimension
        self.attention_conv = nn.Conv2d(self.in_planes, self.middle_dim, [1, 1])
        self.attention_tconv = nn.Conv1d(self.middle_dim, 1, 3, padding=1)
        self.attention_conv.apply(weights_init_kaiming)
        self.attention_tconv.apply(weights_init_kaiming)
        #------------------------------------------
        self.shift_num = 5
        self.part = 4
        self.rearrange = True 

    def forward(self, x, label=None, cam_label=None, view_label=None):
        """
        수정된 forward 함수:
          - 입력 x가 5차원([B, T, C, H, W])인 경우 raw 이미지로 간주하여 self.base를 통해 patch embedding을 수행.
          - 입력 x가 4차원([B, T, num_tokens, embed_dim])인 경우, 이미 FusionToken을 적용해 융합된 토큰으로 간주.
        """
        if x.dim() == 5:
            # Raw image 입력: [B, T, C, H, W]
            b = x.size(0)
            t = x.size(1)
            x = x.view(b * t, x.size(2), x.size(3), x.size(4))
            features = self.base(x, cam_label=cam_label)
        elif x.dim() == 4:
            # 이미 융합된 토큰 입력: [B, T, num_tokens, embed_dim]
            b = x.size(0)
            t = x.size(1)
            features = x.view(b * t, x.size(2), x.size(3))
        else:
            raise ValueError("입력 텐서의 차원이 예상 범위를 벗어났습니다.")

        # global branch
        b1_feat = self.b1(features)  # [B*T, token_length, 768]
        global_feat = b1_feat[:, 0]   # CLS 토큰

        global_feat = global_feat.unsqueeze(dim=2).unsqueeze(dim=3)
        a = F.relu(self.attention_conv(global_feat))
        a = a.view(b, t, self.middle_dim)
        a = a.permute(0, 2, 1)
        a = F.relu(self.attention_tconv(a))
        a = a.view(b, t)
        a_vals = a

        a = F.softmax(a, dim=1)
        x_att = global_feat.view(b, t, -1)
        a = torch.unsqueeze(a, -1)
        a = a.expand(b, t, self.in_planes)
        att_x = torch.mul(x_att, a)
        att_x = torch.sum(att_x, 1)

        global_feat = att_x.view(b, self.in_planes)
        feat = self.bottleneck(global_feat)

        #-------------------------------------------------
        # video patch part features
        feature_length = features.size(1) - 1
        patch_length = feature_length // 4

        # Temporal clip shift and shuffle
        x_shuffled, token = TCSS(features, self.shift_num, b, t)

        # part1
        part1 = x_shuffled[:, :patch_length]
        part1 = self.b2(torch.cat((token, part1), dim=1))
        part1_f = part1[:, 0]

        # part2
        part2 = x_shuffled[:, patch_length:patch_length * 2]
        part2 = self.b2(torch.cat((token, part2), dim=1))
        part2_f = part2[:, 0]

        # part3
        part3 = x_shuffled[:, patch_length * 2:patch_length * 3]
        part3 = self.b2(torch.cat((token, part3), dim=1))
        part3_f = part3[:, 0]

        # part4
        part4 = x_shuffled[:, patch_length * 3:patch_length * 4]
        part4 = self.b2(torch.cat((token, part4), dim=1))
        part4_f = part4[:, 0]

        part1_bn = self.bottleneck_1(part1_f)
        part2_bn = self.bottleneck_2(part2_f)
        part3_bn = self.bottleneck_3(part3_f)
        part4_bn = self.bottleneck_4(part4_f)

        if self.training:
            Global_ID = self.classifier(feat)
            Local_ID1 = self.classifier_1(part1_bn)
            Local_ID2 = self.classifier_2(part2_bn)
            Local_ID3 = self.classifier_3(part3_bn)
            Local_ID4 = self.classifier_4(part4_bn)
            return [Global_ID, Local_ID1, Local_ID2, Local_ID3, Local_ID4], [global_feat, part1_f, part2_f, part3_f, part4_f], a_vals
        else:
            return torch.cat([feat, part1_bn / 4, part2_bn / 4, part3_bn / 4, part4_bn / 4], dim=1)

    def load_param(self, trained_path, load=False):
        print("load_param실행")
        if not load:
            param_dict = torch.load(trained_path, map_location='cpu')
        else:
            param_dict = trained_path

        # state_dict 정리
        if 'model' in param_dict:
            param_dict = param_dict['model']
        if 'state_dict' in param_dict:
            param_dict = param_dict['state_dict']

        model_dict = self.state_dict()
        new_param_dict = {}

        for k, v in param_dict.items():
            if 'head' in k or 'dist' in k:
                continue  # classifier 관련 파라미터는 로드하지 않음

            # 패치 임베딩 Conv 기반 변환 처리
            if 'patch_embed.proj.weight' in k and len(v.shape) < 4:
                O, I, H, W = self.base.patch_embed.proj.weight.shape
                v = v.reshape(O, -1, H, W)
            # Positional Embedding 크기 조정
            elif k == 'pos_embed' and v.shape != self.base.pos_embed.shape:
                v = resize_pos_embed(v, self.base.pos_embed, self.base.patch_embed.num_y, self.base.patch_embed.num_x)

            # `base.` prefix 처리
            new_k = k
            if k.startswith("base.") and k[5:] in model_dict:
                new_k = k[5:]
            elif not k.startswith("base.") and ("base." + k) in model_dict:
                new_k = "base." + k

            # Cam 크기 자동 조정 처리
            if new_k in ['Cam', 'base.Cam'] and new_k in model_dict:
                expected_shape = model_dict[new_k].shape
                print(f"🔍 [Before Resizing] {new_k}: {v.shape} -> Expected: {expected_shape}")
                if v.shape[0] > expected_shape[0]:
                    v = v[:expected_shape[0], :, :]
                elif v.shape[0] < expected_shape[0]:
                    new_v = torch.randn(expected_shape)
                    new_v[:v.shape[0], :, :] = v
                    v = new_v
                print(f"✅ [After Resizing] {new_k}: {v.shape}")
                new_param_dict[new_k] = v
                continue

            if new_k in model_dict and model_dict[new_k].shape == v.shape:
                new_param_dict[new_k] = v

        model_dict.update(new_param_dict)
        self.load_state_dict(model_dict, strict=False)
        print("✅ Checkpoint loaded successfully.")
