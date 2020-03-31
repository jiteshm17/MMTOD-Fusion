# --------------------------------------------------------
# Pytorch multi-GPU Faster R-CNN
# Licensed under The MIT License [see LICENSE for details]
# Written by Jiasen Lu, Jianwei Yang, based on code from Ross Girshick
# --------------------------------------------------------
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _init_paths
import os
import sys
import numpy as np
import argparse
import pprint
import pdb
import time
from PIL import Image
import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim
import torchvision.transforms.functional as TF
import torchvision.transforms as transforms
from torch.utils.data.sampler import Sampler

from roi_data_layer.roidb import combined_roidb
from roi_data_layer.roibatchLoader import roibatchLoader
from model.utils.config import cfg, cfg_from_file, cfg_from_list, get_output_dir
from model.utils.net_utils import weights_normal_init, save_net, load_net, \
      adjust_learning_rate, save_checkpoint, clip_gradient, printnorm, printgradnorm

import sys
sys.path.insert(0, './lib/model/unit')
from model.unit.utils import get_config, pytorch03_to_pytorch04
from model.unit.trainer import MUNIT_Trainer, UNIT_Trainer
from model.unit.networks_test import VAEGenA, VAEGenB

import torchvision.utils as vutils
from PIL import Image

from copy import deepcopy

from model.faster_rcnn.resnet_dual import resnet

from collections import OrderedDict


def parse_args():
  """
  Parse input arguments
  """
  parser = argparse.ArgumentParser(description='Train a Fast R-CNN network')
  parser.add_argument('--dataset', dest='dataset',
                      help='training dataset',
                      default='pascal_voc', type=str)
  parser.add_argument('--net', dest='net',
                    help='vgg16, res101',
                    default='vgg16', type=str)
  parser.add_argument('--start_epoch', dest='start_epoch',
                      help='starting epoch',
                      default=1, type=int)
  parser.add_argument('--epochs', dest='max_epochs',
                      help='number of epochs to train',
                      default=20, type=int)
  parser.add_argument('--disp_interval', dest='disp_interval',
                      help='number of iterations to display',
                      default=100, type=int)
  parser.add_argument('--checkpoint_interval', dest='checkpoint_interval',
                      help='number of iterations to display',
                      default=10000, type=int)

  parser.add_argument('--save_dir', dest='save_dir',
                      help='directory to save models', default="models",
                      type=str)
  parser.add_argument('--nw', dest='num_workers',
                      help='number of worker to load data',
                      default=0, type=int)
  parser.add_argument('--cuda', dest='cuda',
                      help='whether use CUDA',
                      action='store_true')
  parser.add_argument('--ls', dest='large_scale',
                      help='whether use large imag scale',
                      action='store_true')
  parser.add_argument('--mGPUs', dest='mGPUs',
                      help='whether use multiple GPUs',
                      action='store_true')
  parser.add_argument('--bs', dest='batch_size',
                      help='batch_size',
                      default=1, type=int)
  parser.add_argument('--cag', dest='class_agnostic',
                      help='whether perform class_agnostic bbox regression',
                      action='store_true')

# config optimization
  parser.add_argument('--o', dest='optimizer',
                      help='training optimizer',
                      default="sgd", type=str)
  parser.add_argument('--lr', dest='lr',
                      help='starting learning rate',
                      default=0.001, type=float)
  parser.add_argument('--lr_decay_step', dest='lr_decay_step',
                      help='step to do learning rate decay, unit is epoch',
                      default=5, type=int)
  parser.add_argument('--lr_decay_gamma', dest='lr_decay_gamma',
                      help='learning rate decay ratio',
                      default=0.1, type=float)

# set training session
  parser.add_argument('--s', dest='session',
                      help='training session',
                      default=1, type=int)

# resume trained model
  parser.add_argument('--r', dest='resume',
                      help='resume checkpoint or not',
                      default=False, type=bool)
  parser.add_argument('--checksession', dest='checksession',
                      help='checksession to load model',
                      default=1, type=int)
  parser.add_argument('--checkepoch', dest='checkepoch',
                      help='checkepoch to load model',
                      default=1, type=int)
  parser.add_argument('--checkpoint', dest='checkpoint',
                      help='checkpoint to load model',
                      default=0, type=int)
# log and diaplay
  parser.add_argument('--use_tfb', dest='use_tfboard',
                      help='whether use tensorboard',
                      action='store_true')

  parser.add_argument('--config', default='./lib/model/unit/configs/unit_rgb2thermal_folder.yaml', type=str, help="net configuration")
  parser.add_argument('--input', default=None, type=str, help="input image path")
  parser.add_argument('--output_folder', default='.', type=str, help="output image path")
  parser.add_argument('--checkpoint_unit', default='./lib/model/unit/models/rgb2thermal.pt', type=str, help="checkpoint of autoencoders")
  parser.add_argument('--style', type=str, default='', help="style image path")
  parser.add_argument('--a2b', type=int, default=0, help="1 for a2b and others for b2a")
  parser.add_argument('--seed', type=int, default=10, help="random seed")
  parser.add_argument('--num_style',type=int, default=10, help="number of styles to sample")
  parser.add_argument('--synchronized', action='store_true', help="whether use synchronized style code or not")
  parser.add_argument('--output_only', action='store_true', help="whether use synchronized style code or not")
  parser.add_argument('--output_path', type=str, default='.', help="path for logs, checkpoints, and VGG model weight")
  parser.add_argument('--trainer', type=str, default='UNIT', help="MUNIT|UNIT")

  args = parser.parse_args()

  return args


# def get_unit_models(opts):

#     config = get_config(opts.config)
#     opts.num_style = 1 if opts.style != '' else opts.num_style
#     config['vgg_model_path'] = opts.output_path
#     trainer = UNIT_Trainer(config)
#     try:
#         state_dict = torch.load(opts.checkpoint_unit)
#         trainer.gen_a.load_state_dict(state_dict['a'])
#         trainer.gen_b.load_state_dict(state_dict['b'])
#     except:
#         state_dict = pytorch03_to_pytorch04(torch.load(opts.checkpoint_unit))
#         trainer.gen_a.load_state_dict(state_dict['a'])
#         trainer.gen_b.load_state_dict(state_dict['b'])
#     trainer.cuda()
#     trainer.eval()
#     encode = trainer.gen_a.encode if opts.a2b else trainer.gen_b.encode # encode function
#     style_encode = trainer.gen_b.encode if opts.a2b else trainer.gen_a.encode # encode function
#     decode = trainer.gen_b.decode if opts.a2b else trainer.gen_a.decode # decode function

#     return encode, decode

class Resize_GPU(nn.Module):
    def __init__(self, h, w):
        super(Resize_GPU, self).__init__()
        self.op =  nn.AdaptiveAvgPool2d((h,w))
    def forward(self, x):
        x = self.op(x)
        return x

class sampler(Sampler):
  def __init__(self, train_size, batch_size):
    self.num_data = train_size
    self.num_per_batch = int(train_size / batch_size)
    self.batch_size = batch_size
    self.range = torch.arange(0,batch_size).view(1, batch_size).long()
    self.leftover_flag = False
    if train_size % batch_size:
      self.leftover = torch.arange(self.num_per_batch*batch_size, train_size).long()
      self.leftover_flag = True

  def __iter__(self):
    rand_num = torch.randperm(self.num_per_batch).view(-1,1) * self.batch_size
    self.rand_num = rand_num.expand(self.num_per_batch, self.batch_size) + self.range

    self.rand_num_view = self.rand_num.view(-1)

    if self.leftover_flag:
      self.rand_num_view = torch.cat((self.rand_num_view, self.leftover),0)

    return iter(self.rand_num_view)

  def __len__(self):
    return self.num_data

if __name__ == '__main__':

  args = parse_args()

  print('Called with args:')
  print(args)

  if args.dataset == "pascal_voc":
      args.imdb_name = "voc_2007_trainval"
      args.imdbval_name = "voc_2007_test"
      args.set_cfgs = ['ANCHOR_SCALES', '[8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '20']
  elif args.dataset == "pascal_voc_0712":
      args.imdb_name = "voc_2007_trainval+voc_2012_trainval"
      args.imdbval_name = "voc_2007_test"
      args.set_cfgs = ['ANCHOR_SCALES', '[8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '20']
  elif args.dataset == "coco":
      args.imdb_name = "coco_2014_train+coco_2014_valminusminival"
      args.imdbval_name = "coco_2014_minival"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '50']
  elif args.dataset == "imagenet":
      args.imdb_name = "imagenet_train"
      args.imdbval_name = "imagenet_val"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '30']
  elif args.dataset == "vg":
      # train sizes: train, smalltrain, minitrain
      # train scale: ['150-50-20', '150-50-50', '500-150-80', '750-250-150', '1750-700-450', '1600-400-20']
      args.imdb_name = "vg_150-50-50_minitrain"
      args.imdbval_name = "vg_150-50-50_minival"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '50']

  args.cfg_file = "cfgs/{}_ls.yml".format(args.net) if args.large_scale else "cfgs/{}.yml".format(args.net)

  if args.cfg_file is not None:
    cfg_from_file(args.cfg_file)
  if args.set_cfgs is not None:
    cfg_from_list(args.set_cfgs)

  print('Using config:')
  # pprint.pprint(cfg)
  np.random.seed(cfg.RNG_SEED)

  #torch.backends.cudnn.benchmark = True
  if torch.cuda.is_available() and not args.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

  # train set
  # -- Note: Use validation set and disable the flipped to enable faster loading.
  cfg.TRAIN.USE_FLIPPED = False
  cfg.USE_GPU_NMS = args.cuda
  imdb, roidb, ratio_list, ratio_index = combined_roidb(args.imdb_name)
  train_size = len(roidb)

  print('{:d} roidb entries'.format(len(roidb)))

  output_dir = args.save_dir + "/" + args.net + "/" + args.dataset
  if not os.path.exists(output_dir):
    os.makedirs(output_dir)

  sampler_batch = sampler(train_size, args.batch_size)

  dataset = roibatchLoader(roidb, imdb,ratio_list, ratio_index, args.batch_size, \
                           imdb.num_classes, training=True)

  dataloader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size,
                            sampler=sampler_batch, num_workers=args.num_workers)

  # initilize the tensor holder here.
  im_data = torch.FloatTensor(1)
  im_info = torch.FloatTensor(1)
  num_boxes = torch.LongTensor(1)
  gt_boxes = torch.FloatTensor(1)

  # ship to cuda
  if args.cuda:
    im_data = im_data.cuda()
    im_info = im_info.cuda()
    num_boxes = num_boxes.cuda()
    gt_boxes = gt_boxes.cuda()

  # make variable
  im_data = Variable(im_data)
  im_info = Variable(im_info)
  num_boxes = Variable(num_boxes)
  gt_boxes = Variable(gt_boxes)

  if args.cuda:
    cfg.CUDA = True

  # initilize the network here.
  if args.net in ['res101_unit_update_coco']:
    fasterRCNN = resnet(imdb.classes, 101, pretrained=True, class_agnostic=args.class_agnostic)
  else:
    print("network is not defined")
    pdb.set_trace()

  fasterRCNN.create_architecture()
  # ckpt = torch.load('./lib/model/unit/models/rgb2thermal.pt')
  # print('\n\n\n\n****Loaded GAN weights****\n')
  config = get_config(args.config)

  # gen_a = VAEGenA(config['input_dim_a'], config['gen'])
  # gen_b = VAEGenB(config['input_dim_b'], config['gen'])
  
  # gen_a.load_state_dict(ckpt['a'])
  # gen_b.load_state_dict(ckpt['b'])
  
  # gen_a = gen_a.cuda()
  # gen_b = gen_b.cuda()

  # for p in gen_a.parameters(): p.requires_grad = False

  # for p in gen_b.parameters(): p.requires_grad = False
  
  # for key, p in gen_a.named_parameters(): p.requires_grad = True

  # for key, p in gen_b.named_parameters(): p.requires_grad = True

  # def set_in_fix(m):
  #   classname = m.__class__.__name__
  #   if classname.find('InstanceNorm') != -1:
  #     for p in m.parameters(): p.requires_grad=False

  # gen_a.apply(set_in_fix)
  # gen_b.apply(set_in_fix)

  lr = cfg.TRAIN.LEARNING_RATE
  lr = args.lr
  #tr_momentum = cfg.TRAIN.MOMENTUM
  #tr_momentum = args.momentum

  params = []
  for key, value in dict(fasterRCNN.named_parameters()).items():
    if value.requires_grad:
      if 'bias' in key:
        params += [{'params':[value],'lr':lr*(cfg.TRAIN.DOUBLE_BIAS + 1), \
                'weight_decay': cfg.TRAIN.BIAS_DECAY and cfg.TRAIN.WEIGHT_DECAY or 0}]
      else:
        params += [{'params':[value],'lr':lr, 'weight_decay': cfg.TRAIN.WEIGHT_DECAY}]

  # for key, value in dict(gen_a.named_parameters()).items():
  #   if value.requires_grad:
  #     if 'bias' in key:
  #       params += [{'params':[value],'lr':lr*(cfg.TRAIN.DOUBLE_BIAS + 1), \
  #               'weight_decay': cfg.TRAIN.BIAS_DECAY and cfg.TRAIN.WEIGHT_DECAY or 0}]
  #     else:
  #       params += [{'params':[value],'lr':lr, 'weight_decay': cfg.TRAIN.WEIGHT_DECAY}]

  # for key, value in dict(gen_b.named_parameters()).items():
  #   if value.requires_grad:
  #     if 'bias' in key:
  #       params += [{'params':[value],'lr':lr*(cfg.TRAIN.DOUBLE_BIAS + 1), \
  #               'weight_decay': cfg.TRAIN.BIAS_DECAY and cfg.TRAIN.WEIGHT_DECAY or 0}]
  #     else:
  #       params += [{'params':[value],'lr':lr, 'weight_decay': cfg.TRAIN.WEIGHT_DECAY}]

  if args.optimizer == "adam":
    lr = lr * 0.1
    optimizer = torch.optim.Adam(params)

  elif args.optimizer == "sgd":
    optimizer = torch.optim.SGD(params, momentum=cfg.TRAIN.MOMENTUM)

  # checkpoint_1 = torch.load('./models/res101_coco/coco/faster_rcnn_1_15_19903.pth')
  # checkpoint_2 = torch.load('./models/res101_thermal/pascal_voc/faster_rcnn_1_15_1963.pth')

  # checkpoint_1_model = OrderedDict([(k.replace('RCNN_base', 'RCNN_base_2'), v) for k, v in checkpoint_1['model'].items()  if 'RCNN_base' in k ])

  # checkpoint_2_model = OrderedDict([(k.replace('RCNN_base', 'RCNN_base_1'), v) if 'RCNN_base' in k else (k, v) for k, v in checkpoint_2['model'].items()])

  # checkpoint_2_model.update(checkpoint_1_model)

  # checkpoint_2_model['RCNN_base_3.op.weight'] = fasterRCNN.state_dict()['RCNN_base_3.op.weight']
  # checkpoint_2_model['RCNN_base_3.op.bias'] = fasterRCNN.state_dict()['RCNN_base_3.op.bias']


  # fasterRCNN.load_state_dict(checkpoint_2_model)
  # args.session = checkpoint_2['session']
  # args.start_epoch = checkpoint_1['epoch']

  # optimizer.load_state_dict(checkpoint_1['optimizer'])

  # if 'pooling_mode' in checkpoint_2.keys():
  #     cfg.POOLING_MODE = checkpoint_2['pooling_mode']


  if args.resume:
    load_name = os.path.join(output_dir,
      'faster_rcnn_{}_{}_{}.pth'.format(args.checksession, args.checkepoch, args.checkpoint))
    print("loading checkpoint %s" % (load_name))
    checkpoint = torch.load(load_name)
    args.session = checkpoint['session']
    args.start_epoch = checkpoint['epoch']
    fasterRCNN.load_state_dict(checkpoint['model'])
    #optimizer.load_state_dict(checkpoint['optimizer'])
    #lr = optimizer.param_groups[0]['lr']
    if 'pooling_mode' in checkpoint.keys():
      cfg.POOLING_MODE = checkpoint['pooling_mode']
    print("loaded checkpoint %s" % (load_name))

  if args.mGPUs:
    fasterRCNN = nn.DataParallel(fasterRCNN)

  if args.cuda:
    fasterRCNN.cuda()

  iters_per_epoch = int(train_size / args.batch_size)

  # if args.use_tfboard:
  #   from tensorboardX import SummaryWriter
  #   logger = SummaryWriter(f'logs/{cfg.EXP_DIR}/')
  
  for epoch in range(args.start_epoch, args.max_epochs + 1):
    # setting to train mode
    fasterRCNN.train()
    count = 0
    # gen_a.train()
    # gen_b.train()
    loss_temp = 0
    start = time.time()

    if epoch % (args.lr_decay_step + 1) == 0:
        adjust_learning_rate(optimizer, args.lr_decay_gamma)
        lr *= args.lr_decay_gamma

    data_iter = iter(dataloader)
    for step in range(iters_per_epoch):
      data = next(data_iter)
      with torch.no_grad():
        im_data.resize_(data[0].size()).copy_(data[0])
        im_info.resize_(data[1].size()).copy_(data[1])
        gt_boxes.resize_(data[2].size()).copy_(data[2])
        num_boxes.resize_(data[3].size()).copy_(data[3])

      im_shape = im_data.size()
    
      nw_resize = Resize_GPU(im_shape[2], im_shape[3])

      # gen_a.zero_grad()
      # gen_b.zero_grad()

      rgb_path = data[4][0]
      
      # base_path = '/media/charan/Data/charan/surya/git-repo/MMTOD/data/VOCdevkit2007/VOC2007/RGB_Images/'
      # base_path = '/media/charan/Data/charan/surya/git-repo/MMTOD/data/VOCdevkit2007/VOC2007/JPEGImages/'
      
      # remaining_digits = 5-len(a)
      # zeros_temp = '0'*remaining_digits
      # img_name = 'FLIR_'+zeros_temp+a+'.jpg'

      
      # print(count)
      # try:
      # img = Image.open(os.path.join(base_path,img_name))
      img = Image.open(rgb_path)
      # except:
      #   print('exception',a)
      #   count = 0
      #   continue
      img = np.array(img)
      img = TF.to_tensor(img)
      img = img.unsqueeze_(0)
      # count += 1 

      # content, _ = gen_b(im_data) # generate rgb image
      # outputs = gen_a(content)
      # im_data_1 = (outputs + 1) / 2.
      
      im_data_1 = nw_resize(img)
      im_data_1 = im_data_1.cuda()


      # vutils.save_image(im_data.data, './input.png', padding=0, normalize=True)
      # vutils.save_image(im_data_1.data, './converted_im.png',  padding=0, normalize=True)

      fasterRCNN.zero_grad()
      rois, cls_prob, bbox_pred, \
      rpn_loss_cls, rpn_loss_box, \
      RCNN_loss_cls, RCNN_loss_bbox, \
      rois_label = fasterRCNN(im_data_1, im_data, im_info, gt_boxes, num_boxes)
      
    #   gen_b.register_backward_hook(printgradnorm)

      loss = rpn_loss_cls.mean() + rpn_loss_box.mean() \
           + RCNN_loss_cls.mean() + RCNN_loss_bbox.mean()
      loss_temp += loss.item()

      # backward
      optimizer.zero_grad()
      loss.backward()
      if args.net == "vgg16":
          clip_gradient(fasterRCNN, 10.)
      optimizer.step()

      if step % args.disp_interval == 0:
        end = time.time()
        if step > 0:
          loss_temp /= (args.disp_interval + 1)

        if args.mGPUs:
          loss_rpn_cls = rpn_loss_cls.mean().item()
          loss_rpn_box = rpn_loss_box.mean().item()
          loss_rcnn_cls = RCNN_loss_cls.mean().item()
          loss_rcnn_box = RCNN_loss_bbox.mean().item()
          fg_cnt = torch.sum(rois_label.data.ne(0))
          bg_cnt = rois_label.data.numel() - fg_cnt
        else:
          loss_rpn_cls = rpn_loss_cls.item()
          loss_rpn_box = rpn_loss_box.item()
          loss_rcnn_cls = RCNN_loss_cls.item()
          loss_rcnn_box = RCNN_loss_bbox.item()
          fg_cnt = torch.sum(rois_label.data.ne(0))
          bg_cnt = rois_label.data.numel() - fg_cnt

        print("[session %d][epoch %2d][iter %4d/%4d] loss: %.4f, lr: %.2e" \
                                % (args.session, epoch, step, iters_per_epoch, loss_temp, lr))
        print("\t\t\tfg/bg=(%d/%d), time cost: %f" % (fg_cnt, bg_cnt, end-start))
        print("\t\t\trpn_cls: %.4f, rpn_box: %.4f, rcnn_cls: %.4f, rcnn_box %.4f" \
                      % (loss_rpn_cls, loss_rpn_box, loss_rcnn_cls, loss_rcnn_box))
        if args.use_tfboard:
          info = {
            'loss': loss_temp,
            'loss_rpn_cls': loss_rpn_cls,
            'loss_rpn_box': loss_rpn_box,
            'loss_rcnn_cls': loss_rcnn_cls,
            'loss_rcnn_box': loss_rcnn_box,
            # 'loss_feat': loss_feat
          }
          logger.add_scalars("logs_s_{}/losses".format(args.session), info, (epoch - 1) * iters_per_epoch + step)

          import torchvision.utils as vutils
          x1 = vutils.make_grid(im_data, normalize=True, scale_each=True)
          logger.add_image("images_s_{}/original_thermal_image".format(args.session), x1, (epoch - 1) * iters_per_epoch + step)
          # logger.add_images("images_s_{}/frcnn_input_image".format(args.session), im_data, (epoch - 1) * iters_per_epoch + step)
          x2 = vutils.make_grid(im_data_1, normalize=True, scale_each=True)
          logger.add_image("images_s_{}/generated_rgb".format(args.session), x2, (epoch - 1) * iters_per_epoch + step)
          # logger.add_images("images_s_{}/unnormed_first_domain".format(args.session), im_data_1, (epoch - 1) * iters_per_epoch + step)

          # x4 = vutils.make_grid(im_data_1ch, normalize=True, scale_each=True)
          # logger.add_image("images_s_{}/thermal_1channel".format(args.session), x4, (epoch - 1) * iters_per_epoch + step)

        loss_temp = 0
        start = time.time()


    save_name = os.path.join(output_dir, 'faster_rcnn_{}_{}_{}.pth'.format(args.session, epoch, step))
    # save_name_gen_a = os.path.join(output_dir, 'gen_a_{}_{}_{}.pth'.format(args.session, epoch, step))
    # save_name_gen_b = os.path.join(output_dir, 'gen_b_{}_{}_{}.pth'.format(args.session, epoch, step))

    save_checkpoint({
      'session': args.session,
      'epoch': epoch + 1,
      'model': fasterRCNN.module.state_dict() if args.mGPUs else fasterRCNN.state_dict(),
      'optimizer': optimizer.state_dict(),
      'pooling_mode': cfg.POOLING_MODE,
      'class_agnostic': args.class_agnostic,
    }, save_name)
    # save_checkpoint({
    #   'model': gen_a.state_dict(),
    # }, save_name_gen_a)
    # save_checkpoint({
    #   'model': gen_b.state_dict(),
    # }, save_name_gen_b)

    print('save model: {}'.format(save_name))

  if args.use_tfboard:
    logger.close()
