# Copyright (c) 2021-2022, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.2
import tensorflow as tf

import model_navigator.framework_api as nav

dataloader = [tf.random.uniform(shape=[1, 224, 224, 3], minval=0, maxval=1, dtype=tf.dtypes.float32) for _ in range(10)]

inp = tf.keras.layers.Input((224, 224, 3))
layer_output = tf.keras.layers.Lambda(lambda x: x)(inp)
layer_output = tf.keras.layers.Lambda(lambda x: x)(layer_output)
layer_output = tf.keras.layers.Lambda(lambda x: x)(layer_output)
layer_output = tf.keras.layers.Lambda(lambda x: x)(layer_output)
layer_output = tf.keras.layers.Lambda(lambda x: x)(layer_output)
model_output = tf.keras.layers.Lambda(lambda x: x)(layer_output)
model = tf.keras.Model(inp, model_output)


nav.tensorflow.export(
    model=model,
    dataloader=dataloader,
    zip_package=True,
    override_workdir=True,
)
