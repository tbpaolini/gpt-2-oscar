#!/usr/bin/env python3

from __future__ import annotations

from urllib import response
import fire
import json
import os
import numpy as np
import tensorflow._api.v2.compat.v1 as tf

import model, sample, encoder

import multiprocessing as mp

"""
The model will be run in a separate process. It will use an input queue to get
messages, and an output queue to place its responses.

The itens of the queues is a tuple of two elements:
    - the message itself as a string (index 0)
    - an arbitrary integer number to match the input with the output (index 1)

When the sentinel object STOP is found at the queues, it indicates that the
queues should stop being processed, and the program should exit.
"""

STOP = "STOP"
UserText:mp.Queue[tuple[str,int]]

def interact_model(
    model_name='oscar2',
    seed=None,
    nsamples=1,
    batch_size=1,
    length=40,
    temperature=0.8,
    top_k=40,
    top_p=0.9,
    models_dir='models',
    input_queue:UserText=None,
    output_queue:UserText=None,
):
    """
    Interactively run the model
    :model_name=124M : String, which model to use
    :seed=None : Integer seed for random number generators, fix seed to reproduce
     results
    :nsamples=1 : Number of samples to return total
    :batch_size=1 : Number of batches (only affects speed/memory).  Must divide nsamples.
    :length=None : Number of tokens in generated text, if None (default), is
     determined by model hyperparameters
    :temperature=1 : Float value controlling randomness in boltzmann
     distribution. Lower temperature results in less random completions. As the
     temperature approaches zero, the model will become deterministic and
     repetitive. Higher temperature results in more random completions.
    :top_k=0 : Integer value controlling diversity. 1 means only 1 word is
     considered for each step (token), resulting in deterministic completions,
     while 40 means 40 words are considered at each step. 0 (default) is a
     special setting meaning no restrictions. 40 generally is a good value.
     :models_dir : path to parent folder containing model subfolders
     (i.e. contains the <model_name> folder)
    """
    models_dir = os.path.expanduser(os.path.expandvars(models_dir))
    if batch_size is None:
        batch_size = 1
    assert nsamples % batch_size == 0

    enc = encoder.get_encoder(model_name, models_dir)
    hparams = model.default_hparams()
    with open(os.path.join(models_dir, model_name, 'hparams.json')) as f:
        hparams.override_from_dict(json.load(f))

    if length is None:
        length = hparams.n_ctx // 2
    elif length > hparams.n_ctx:
        raise ValueError("Can't get samples longer than window size: %s" % hparams.n_ctx)

    with tf.Session(graph=tf.Graph()) as sess:
        context = tf.placeholder(tf.int32, [batch_size, None])
        np.random.seed(seed)
        tf.set_random_seed(seed)
        output = sample.sample_sequence(
            hparams=hparams, length=length,
            context=context,
            batch_size=batch_size,
            temperature=temperature, top_k=top_k, top_p=top_p
        )

        saver = tf.train.Saver()
        ckpt = tf.train.latest_checkpoint(os.path.join(models_dir, model_name))
        saver.restore(sess, ckpt)

        print("-" * 40 + "\nBot is ready! Listening for messages.\n" + "-" * 40)
        while True:
            next_item = input_queue.get()
            if next_item == STOP:
                output_queue.put(STOP, block=False)
                break
            platform, raw_text, response_id = next_item
            context_tokens = enc.encode(raw_text)
            for _ in range(nsamples // batch_size):
                out = sess.run(output, feed_dict={
                    context: [context_tokens for _ in range(batch_size)]
                })[:, len(context_tokens):]
                for i in range(batch_size):
                    text = enc.decode(out[i])
                    output_queue.put((platform, text, response_id), block=False)

if __name__ == '__main__':
    fire.Fire(interact_model)
