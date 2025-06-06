from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import os
import os.path as osp
import glob
import warnings

from ..dataset import ImageDataset

# Sources :
# https://github.com/hh23333/PVPM
# Z.D. Zheng, L. Zheng, and Y. Yang, "Unlabeled samples generated by gan improve the person re-identification baseline in vitro" in ICCV, 2017.

class PDukemtmcReid(ImageDataset):
    dataset_dir = 'P-DukeMTMC-reID'
    masks_base_dir = 'masks'
    train_dir = 'train'
    query_dir = 'test/occluded_body_images'
    gallery_dir = 'test/whole_body_images'

    masks_dirs = {
        # dir_name: (parts_num, masks_stack_size, contains_background_mask)
        'pifpaf': (36, False, '.jpg.confidence_fields.npy'),
        'pifpaf_maskrcnn_filtering': (36, False, '.npy'),
    }

    @staticmethod
    def get_masks_config(masks_dir):
        if masks_dir not in PDukemtmcReid.masks_dirs:
            return None
        else:
            return PDukemtmcReid.masks_dirs[masks_dir]

    def infer_masks_path(self, img_path):
        masks_path = os.path.join(self.dataset_dir,
                                  self.masks_base_dir,
                                  self.masks_dir,
                                  os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(img_path)))),
                                  os.path.basename(os.path.dirname(os.path.dirname(img_path))),
                                  # FIXME ugly
                                  os.path.splitext(os.path.basename(img_path))[0] + self.masks_suffix)
        return masks_path

    def __init__(self, root='', masks_dir=None, **kwargs):
        self.kp_dir = kwargs['config'].model.kpr.keypoints.kp_dir
        self.masks_dir = masks_dir
        if self.masks_dir in self.masks_dirs:
            self.masks_parts_numbers, self.has_background, self.masks_suffix = self.masks_dirs[self.masks_dir]
        else:
            self.masks_parts_numbers, self.has_background, self.masks_suffix = None, None, None
        self.root = osp.abspath(osp.expanduser(root))
        self.dataset_dir = osp.join(self.root, self.dataset_dir)
        if not osp.isdir(self.dataset_dir):
            warnings.warn(
                'The current data structure is deprecated. Please '
                'put data folders such as "bounding_box_train" under '
                '"Market-1501-v15.09.15".'
            )
        self.train_dir=osp.join(self.dataset_dir, self.train_dir)
        self.query_dir=osp.join(self.dataset_dir, self.query_dir)
        self.gallery_dir=osp.join(self.dataset_dir, self.gallery_dir)

        train = self.process_train_dir(self.train_dir, relabel=True)
        query = self.process_dir(self.query_dir, relabel=False)
        gallery = self.process_dir(self.gallery_dir, relabel=False, is_query=False)
        super(PDukemtmcReid, self).__init__(train, query, gallery, **kwargs)

    def process_train_dir(self, dir_path, relabel=True):
        img_paths = glob.glob(osp.join(dir_path,'whole_body_images', '*', '*.jpg'))
        camid=1
        pid_container = set()
        for img_path in img_paths:
            img_name = img_path.split('/')[-1]
            pid = int(img_name.split('_')[0])
            pid_container.add(pid)
        pid2label = {pid:label for label, pid in enumerate(pid_container)}
        data = []
        for img_path in img_paths:
            img_name = img_path.split('/')[-1]
            pid = int(img_name.split('_')[0])
            if relabel:
                pid = pid2label[pid]
            masks_path = self.infer_masks_path(img_path)
            data.append({'img_path': img_path,
                         'pid': pid,
                         'masks_path': masks_path,
                         'kp_path': kp_path,
                         'camid': camid})
        img_paths = glob.glob(osp.join(dir_path,'occluded_body_images','*','*.jpg'))
        camid=0
        for img_path in img_paths:
            img_name = img_path.split('/')[-1]
            pid = int(img_name.split('_')[0])
            if relabel:
                pid = pid2label[pid]
            masks_path = self.infer_masks_path(img_path)
            data.append({'img_path': img_path,
                         'pid': pid,
                         'masks_path': masks_path,
                         'kp_path': kp_path,
                         'camid': camid})
        return data

    def process_dir(self, dir_path, relabel=False, is_query=True):
        img_paths = glob.glob(osp.join(dir_path, '*', '*.jpg'))
        if is_query:
            camid = 0
        else:
            camid = 1
        pid_container = set()
        for img_path in img_paths:
            img_name = img_path.split('/')[-1]
            pid = int(img_name.split('_')[0])
            pid_container.add(pid)
        pid2label = {pid:label for label, pid in enumerate(pid_container)}

        data = []
        for img_path in img_paths:
            img_name = img_path.split('/')[-1]
            pid = int(img_name.split('_')[0])
            if relabel:
                pid = pid2label[pid]
            masks_path = self.infer_masks_path(img_path)
            kp_path = self.infer_kp_path(img_path)
            data.append({'img_path': img_path,
                         'pid': pid,
                         'masks_path': masks_path,
                         'kp_path': kp_path,
                         'camid': camid})
        return data
