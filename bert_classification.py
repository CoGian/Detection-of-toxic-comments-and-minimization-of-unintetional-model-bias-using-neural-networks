# -*- coding: utf-8 -*-
"""BERT_classification.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1VvYwJivbXmaO62FOm6hpz3zFc_JilzRX
"""

from google.colab import drive
drive.mount('/content/drive' )

import pandas as pd
import tensorflow as tf
!pip install transformers==2.11.0
from transformers import *
print(tf.__version__)
import numpy as np
from tqdm import tqdm
import gc
import sys
sys.path.append('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/tools')
sys.path.append('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification')
from tools_benchmark import  compute_bias_metrics_for_model, calculate_overall_auc,get_final_metric
from tools_evaluate_model import evaluate

"""# TPU Configs"""

# Detect hardware, return appropriate distribution strategy
try:
    # TPU detection. No parameters necessary if TPU_NAME environment variable is
    # set: this is always the case on Kaggle.
    tpu = tf.distribute.cluster_resolver.TPUClusterResolver()
    print('Running on TPU ', tpu.master())
except ValueError:
    tpu = None

if tpu:
    tf.config.experimental_connect_to_cluster(tpu)
    tf.tpu.experimental.initialize_tpu_system(tpu)
    strategy = tf.distribute.experimental.TPUStrategy(tpu)
else:
    # Default distribution strategy in Tensorflow. Works on CPU and single GPU.
    strategy = tf.distribute.get_strategy()

print("REPLICAS: ", strategy.num_replicas_in_sync)

"""# Load Datasets"""

IDENTITY_COLUMNS  = [
    'male', 'female', 'homosexual_gay_or_lesbian', 'christian', 'jewish',
    'muslim', 'black', 'white', 'psychiatric_or_mental_illness'
  ] 
TARGET_COLUMN = 'target'
TOXICITY_COLUMN = 'toxicity'
AUX_COLUMNS = ['target', 'severe_toxicity', 'obscene', 'identity_attack', 'insult', 'threat']

test_public_df = pd.read_csv("/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/data/test_public_cleared.csv")
#test_public_df = test_public_df.loc[:, ['toxicity','comment_text']  + IDENTITY_COLUMNS ].dropna()[:1000]
test_private_df = pd.read_csv("/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/data/test_private_cleared.csv")
#test_private_df = test_private_df.loc[:, ['toxicity', 'comment_text'] + IDENTITY_COLUMNS ].dropna()[:1000]

y_public_test = test_public_df[TOXICITY_COLUMN].values.reshape((-1,1))
y_private_test = test_private_df[TOXICITY_COLUMN].values.reshape((-1,1))

"""# Get datasets


"""

def get_dataset(PATH,forTest = False):
  
  with open('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/data/'+PATH+'/input_ids.npy','rb') as f:
    input_ids = np.load(f)

  with open('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/data/'+PATH+'/input_mask.npy','rb') as f:
    attention_mask = np.load(f)

  with open('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/data/'+PATH+'/segment_ids.npy','rb') as f:
    token_type_ids = np.load(f)

  
  if(forTest):
    return tf.data.Dataset.from_tensor_slices({"input_word_ids" : input_ids , "input_mask" :attention_mask, "segment_ids" : token_type_ids}).batch(BATCH_SIZE)
  else :
    with open('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/data/'+PATH+'/labels.npy','rb') as f:
      labels = np.load(f)
    with open('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/data/'+PATH+'/labels_aux.npy','rb') as f:
      labels_aux =np.load(f)
    with open('/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/data/'+PATH+'/sample_weights.npy','rb') as f:
      sample_weights = np.load(f)
    return tf.data.Dataset.from_tensor_slices(({"input_word_ids" : input_ids , "input_mask" : attention_mask, "segment_ids" : token_type_ids}
                                                    ,{"target": labels, "aux": labels_aux},sample_weights ))

"""# BERT-with-max-avg-pool

## Create Model
"""

def createBERT(bert_layer):
  input_word_ids = tf.keras.layers.Input(shape=(MAX_LEN,), dtype=tf.int32,
                                       name="input_word_ids")
  input_mask = tf.keras.layers.Input(shape=(MAX_LEN,), dtype=tf.int32,
                                   name="input_mask")
  segment_ids = tf.keras.layers.Input(shape=(MAX_LEN,), dtype=tf.int32,
                                    name="segment_ids")
  
  sequence_output , pooled_output = bert_layer([input_word_ids, input_mask, segment_ids])

  avg_pool = tf.keras.layers.GlobalAveragePooling1D()(sequence_output)
  max_pool = tf.keras.layers.GlobalMaxPooling1D()(sequence_output)
  x = tf.keras.layers.concatenate([avg_pool,max_pool])

  x = tf.keras.layers.Dropout(0.2)(x)
  
  result = tf.keras.layers.Dense(1, activation="sigmoid", name="target")(x)
  aux_result =  tf.keras.layers.Dense(6, activation='sigmoid' , name = 'aux')(x)

  model = tf.keras.Model(inputs=[input_word_ids, input_mask, segment_ids], outputs=[result, aux_result])
  model.compile(loss=tf.keras.losses.BinaryCrossentropy(),
              optimizer=tf.keras.optimizers.Adam(learning_rate=2e-5),metrics = ['accuracy'])
  return model

"""## BERT - Base"""

BATCH_SIZE = 64 
EPOCHS = 4
MAX_LEN = 180
BUFFER_SIZE =  np.ceil(1804874 * 0.8)
MODEL = 'bert-base-cased'

AUTO = tf.data.experimental.AUTOTUNE
bert_train_inputs_ds = get_dataset('train').repeat().shuffle(BUFFER_SIZE).batch(BATCH_SIZE).prefetch(AUTO)
gc.collect()
bert_val_inputs_ds = get_dataset('val').batch(BATCH_SIZE).cache().prefetch(AUTO)
gc.collect()
bert_test_public_inputs_ds = get_dataset('test_public',forTest = True)
gc.collect()
bert_test_private_inputs_ds = get_dataset('test_private',forTest = True)
gc.collect()

with strategy.scope():
  bert_layer = TFAutoModel.from_pretrained(MODEL)
  model = createBERT(bert_layer)
model.summary()
tf.keras.utils.plot_model(model, show_shapes= True ,show_layer_names=False, 
                  to_file='/content/drive/My Drive/Jigsaw Unintended Bias in Toxicity Classification/models/BERT/BERT-with-max-avg-pool/BERTwPOOL.png')

n_steps = BUFFER_SIZE // BATCH_SIZE 
for epoch in range(EPOCHS):
  model.fit(x = bert_train_inputs_ds,validation_data=bert_val_inputs_ds , epochs = 1 
                          ,verbose = 1 , steps_per_epoch=n_steps)
  y_public_pred  = model.predict(bert_test_public_inputs_ds, verbose=1 )
  y_private_pred = model.predict(bert_test_private_inputs_ds, verbose=1 )
  evaluate(y_public_pred[0],y_private_pred[0], test_public_df, test_private_df, 'BERT/BERT-with-max-avg-pool/'+ str(epoch+1) + '_epochs' , MODEL)

