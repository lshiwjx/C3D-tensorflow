from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import deform_conv3d_op
import tensorflow as tf

FLAGS = tf.app.flags.FLAGS
tf.app.flags.DEFINE_float('dropout_ratio', 1, "")
tf.app.flags.DEFINE_float('weight_decay_ratio', 0.0005, "")

TOWER_NAME = 'tower'


def _activation_summary(var):
    """Helper to create summaries for activations.
    Creates a summary that provides a histogram of activations.
    Creates a summary that measures the sparsity of activations.
    Args:
      var: Tensor
    """
    # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
    # tensor_name = re.sub('%s_[0-9]*/' % TOWER_NAME, '', var.op.name)
    # mean = tf.reduce_mean(var)
    # tf.summary.scalar('mean', mean)
    # with tf.name_scope('stddev'):
    #     stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
    # tf.summary.scalar('stddev', stddev)
    # tf.summary.scalar('max', tf.reduce_max(var))
    # tf.summary.scalar('min', tf.reduce_min(var))
    # tf.summary.histogram('activations', var)
    # percentage of zero in the variable
    # tf.summary.scalar('sparsity', tf.nn.zero_fraction(var))


def _variable_on_cpu(name, shape, initializer):
    """Helper to create a Variable stored on CPU memory.
    Args:
      name: name of the variable
      shape: list of ints
      initializer: initializer for Variable
    Returns:
      Variable Tensor
    """
    with tf.device('/cpu:0'):
        dtype = tf.float16 if FLAGS.use_fp16 else tf.float32
        var = tf.get_variable(name, shape, initializer=initializer, dtype=dtype)
    return var


def _variable_with_weight_decay(name, shape, weight_decay_ratio=None, stddev=0.0):
    """Helper to create an initialized Variable with weight decay.
    Args:
      name: name of the variable
      shape: list of ints
      stddev: standard deviation of a truncated Gaussian
      weight_decay_ratio: add L2 Loss weight decay multiplied by this float. 
    Returns:
      Variable Tensor
    """
    dtype = tf.float16 if FLAGS.use_fp16 else tf.float32
    var = _variable_on_cpu(name, shape,
                           tf.truncated_normal_initializer(stddev=stddev, dtype=dtype))  # 截断高斯
    if weight_decay_ratio is not None:
        weight_decay = tf.multiply(tf.nn.l2_loss(var), weight_decay_ratio, name='weight_loss')
        # add the loss of weight decay to losses
        tf.add_to_collection('losses', weight_decay)
        # tf.summary.scalar('weight_loss', weight_decay)
    return var


def deoform_conv_3d(filter_name, biases_name, input,offset,
                    filter_shape, offset_shape, biases_shape,
                    filter_weight_decay=None, offset_weight_decay=None, biases_weight_decay=None,
                    filter_stddev=0.0, offset_stddev=0.0, biases_stddev=0.0):
    """
    
    :param filter_name: 
    :param biases_name: 
    :param input:  [n,c,l,h,w]
    :param offset: [g,l,h,w,l',h',w',3]
    :param filter_shape: [c',l',h',w'] 
    :param offset_shape: [g,l,h,w,l',h',w',3]
    :param biases_shape: [c*c']
    :param filter_weight_decay: 
    :param offset_weight_decay: 
    :param biases_weight_decay: 
    :param filter_stddev: 
    :param offset_stddev: 
    :param biases_stddev: 
    :return: [n,c*c',l",h",w"]->[n,l",h",w",c*c']
    """
    filter = _variable_with_weight_decay(filter_name,filter_shape,filter_weight_decay,filter_stddev)
    input_trans = tf.transpose(input, perm=[0,4,1,2,3])
    deform_conv_trans = deform_conv3d_op.deform_conv3d(input_trans,filter,offset,padding='SAME')
    deform_conv = tf.transpose(deform_conv_trans, perm=[0,2,3,4,1])
    biases=_variable_with_weight_decay(biases_name,biases_shape,biases_weight_decay,biases_stddev)
    pre_activation = tf.nn.bias_add(deform_conv,biases)
    return pre_activation


def conv_3d(filter_name, biases_name, input,
            filter_shape, biases_shape, filter_weight_decay=None, biases_weight_decay=None,
            filter_stddev=0.0, biases_stddev=0.0):
    """Computes a 3-D convolution given 5-D `input` and `filter` tensors.
    
      Args:
          biases_name: 
          filter_name: A name for the operation.
          input: [batch, in_channels, in_depth, in_height, in_width].
          filter_shape: [filter_depth, filter_height, filter_width, filter_channels, out_channels]
          biases_shape: same as filter_channels of filter
          filter_weight_decay:
          biases_weight_decay:
          filter_stddev:
          biases_stddev:
      Returns:
        tensor of convolution result
      """
    filter = _variable_with_weight_decay(filter_name, filter_shape, filter_weight_decay, stddev=filter_stddev)
    conv = tf.nn.conv3d(input, filter, [1, 1, 1, 1, 1], padding='SAME')  # , data_format='NCDHW')
    biases = _variable_with_weight_decay(biases_name, biases_shape, biases_weight_decay, stddev=biases_stddev)
    # conv_trans = tf.transpose(conv, perm=[0, 2, 3, 4, 1])
    pre_activation = tf.nn.bias_add(conv, biases)
    # pre_activation = tf.transpose(pre_activation_trans, perm=[0, 4, 1, 2, 3])
    return pre_activation


def max_pool(name, l_input, depth):
    return tf.nn.max_pool3d(l_input, ksize=[1, depth, 2, 2, 1],
                            strides=[1, depth, 2, 2, 1], padding='SAME',
                            name=name)


def inference_c3d(videos, is_training, is_feature_extractor=False):
    """Generate the 3d convolution classification output according to the input videos
  
    Args:
        videos: Data Input, the shape of the Data Input is [batch_size, channel, length, height, weight]
        is_training: 
        is_feature_extractor: used as feature extractor or not
    Return:
      out: classification result, the shape is [batch_size, num_classes]
    """

    if is_training:
        dropout_ratio = FLAGS.dropout_ratio
    else:
        dropout_ratio = 1

    # print('is training: ', is_training)
    # print('dropout ratio: ', dropout_ratio)
    # print('weight_decay_ratio: ', FLAGS.weight_decay_ratio)

    # Conv1 Layer
    with tf.variable_scope('conv1') as scope:
        # summary image
        # image_summary = tf.transpose(videos, perm=[0, 2, 3, 4, 1])[0]
        tf.summary.image('video', videos[0], max_outputs=1)

        conv1 = conv_3d('weight', 'biases', videos,
                        [3, 3, 3, FLAGS.video_clip_channels, 64], [64],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 3)) ** 0.5)
        conv1 = tf.nn.relu(conv1, name=scope.name)
        # print('\n', scope.name)
        # print('input shape :', videos.shape)
        # print('filter shape: ', [3, 3, 3, FLAGS.video_clip_channels, 64])
        # print('out shape: ', conv1.shape)
        _activation_summary(conv1)

    # pool1
    pool1 = max_pool('pool1', conv1, 1)

    # Conv2 Layer
    with tf.variable_scope('conv2') as scope:
        conv2 = conv_3d('weight', 'biases', pool1,
                        [3, 3, 3, 64, 128], [128],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 64)) ** 0.5)
        conv2 = tf.nn.relu(conv2, name=scope.name)

        visual = tf.expand_dims(tf.transpose(conv2[0],perm=[3,0,1,2]),4)
        tf.summary.image('feature_map', visual[0], 3)
        # print('\n', scope.name)
        # print('input shape :', pool1.shape)
        # print('filter shape: ', [3, 3, 3, 64, 128])
        # print('out shape: ', conv2.shape)
        _activation_summary(conv2)

    # pool2
    pool2 = max_pool('pool2', conv2, 2)

    # Conv3 Layer
    with tf.variable_scope('conv3') as scope:
        conv3 = conv_3d('weight_a', 'biases_a', pool2,
                        [3, 3, 3, 128, 256], [256],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 128)) ** 0.5)
        conv3 = tf.nn.relu(conv3, name=scope.name + 'a')
        # print('\n', scope.name)
        # print('input shape :', pool2.shape)
        # print('filter shape a : ', [3, 3, 3, 128, 256])
        # print('out shape a : ', conv3.shape)
        conv3 = conv_3d('weight_b', 'biases_b', conv3,
                        [3, 3, 3, 256, 256], [256],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 256)) ** 0.5)
        conv3 = tf.nn.relu(conv3, name=scope.name + 'b')
        # print('filter shape b : ', [3, 3, 3, 256, 256])
        # print('out shape b : ', conv3.shape)
        _activation_summary(conv3)

    # pool3
    pool3 = max_pool('pool3', conv3, 2)

    # Conv4 Layer
    with tf.variable_scope('conv4') as scope:
        conv4 = conv_3d('weight_a', 'biases_a', pool3,
                        [3, 3, 3, 256, 512], [512],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 256)) ** 0.5)
        conv4 = tf.nn.relu(conv4, name=scope.name + 'a')
        # print('\n', scope.name)
        # print('input shape :', pool3.shape)
        # print('filter shape a: ', [3, 3, 3, 256, 512])
        # print('out shape a: ', conv4.shape)
        conv4 = conv_3d('weight_b', 'biases_b', conv4,
                        [3, 3, 3, 512, 512], [512],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 512)) ** 0.5)
        conv4 = tf.nn.relu(conv4, name=scope.name + 'b')

        # print('filter shape b: ', [3, 3, 3, 512, 512])
        # print('out shape b: ', conv4.shape)
        _activation_summary(conv4)

    # pool4
    pool4 = max_pool('pool4', conv4, 2)

    # Conv5 Layer
    with tf.variable_scope('conv5') as scope:
        conv5 = conv_3d('weight_a', 'biases_a', pool4,
                        [3, 3, 3, 512, 512], [512],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 512)) ** 0.5)
        conv5 = tf.nn.relu(conv5, name=scope.name + 'a')
        # print('\n', scope.name)
        # print('input shape :', pool4.shape)
        # print('filter shape a : ', [3, 3, 3, 512, 512])
        # print('out shape a : ', conv5.shape)
        conv5 = conv_3d('weight_b', 'biases_b', conv5,
                        [3, 3, 3, 512, 512], [512],
                        filter_weight_decay=FLAGS.weight_decay_ratio,
                        biases_weight_decay=None,
                        filter_stddev=(2.0 / (3 ** 3 * 512)) ** 0.5)
        conv5 = tf.nn.relu(conv5, name=scope.name + 'b')

        # print('filter shape b : ', [3, 3, 3, 512, 512])
        # print('out shape b : ', conv5.shape)
        _activation_summary(conv5)

    # pool5
    pool5 = max_pool('pool5', conv5, 2)

    # local6
    with tf.variable_scope('local6') as scope:
        weights = _variable_with_weight_decay('weights', [8192, 4096],
                                              weight_decay_ratio=FLAGS.weight_decay_ratio,
                                              stddev=1.0 / 8192)
        biases = _variable_with_weight_decay('biases', [4096])
        pool5 = tf.transpose(pool5, perm=[0, 1, 4, 2, 3])
        local6 = tf.reshape(pool5, [-1, weights.get_shape().as_list()[0]])
        local6 = tf.nn.relu(tf.matmul(local6, weights) + biases, name=scope.name)
        if is_feature_extractor:
            return local6
        local6 = tf.nn.dropout(local6, dropout_ratio)
        # print('\n', scope.name)
        # print('input shape :', pool5.shape)
        # print('out shape: ', local6.shape)
        _activation_summary(local6)

    # local7
    with tf.variable_scope('local7') as scope:
        weights = _variable_with_weight_decay('weights', [4096, 4096],
                                              weight_decay_ratio=FLAGS.weight_decay_ratio,
                                              stddev=1.0 / 4096)
        biases = _variable_with_weight_decay('biases', [4096])
        local7 = tf.nn.relu(tf.matmul(local6, weights) + biases, name=scope.name)
        local7 = tf.nn.dropout(local7, dropout_ratio)
        # print('\n', scope.name)
        # print('input shape :', local6.shape)
        # print('out shape: ', local7.shape)
        _activation_summary(local7)

    # linear layer(Wx + b)
    with tf.variable_scope('softmax_lineaer') as scope:
        weights = _variable_with_weight_decay('weights', [4096, FLAGS.num_classes],
                                              weight_decay_ratio=FLAGS.weight_decay_ratio,
                                              stddev=1.0 / 4096)
        biases = _variable_with_weight_decay('biases', [FLAGS.num_classes])
        softmax_linear = tf.add(tf.matmul(local7, weights), biases, name=scope.name)
        # print('\n', scope.name)
        # print('input shape :', local7.shape)
        # print('out shape: ', softmax_linear.shape)
        _activation_summary(softmax_linear)

    return softmax_linear