#!/usr/bin/env python

import click as ck
import numpy as np
import pandas as pd
import tensorflow as tf
import re
import math
import matplotlib.pyplot as plt
import logging

logging.basicConfig(level=logging.INFO)

tf.enable_eager_execution()

@ck.command()
@ck.option(
    '--data-file', '-df', default='go-normalized.txt',
    help='Normalized ontology file (Normalizer.groovy)')
@ck.option(
    '--out-classes-file', '-ocf', default='data/cls_embeddings.pkl',
    help='Pandas pkl file with class embeddings')
@ck.option(
    '--out-relations-file', '-orf', default='data/rel_embeddings.pkl',
    help='Pandas pkl file with relation embeddings')
@ck.option(
    '--batch-size', '-bs', default=256,
    help='Batch size')
@ck.option(
    '--epochs', '-e', default=1024,
    help='Training epochs')
@ck.option(
    '--device', '-d', default='gpu:0',
    help='GPU Device ID')
@ck.option(
    '--embedding-size', '-es', default=100,
    help='Embeddings size')
def main(data_file, out_classes_file, out_relations_file,
         batch_size, epochs, device, embedding_size):
    data, classes, relations = load_data(data_file)
    nb_classes = len(classes)
    nb_relations = len(relations)
    nb_data = 0
    for key, val in data.items():
        nb_data = max(len(val), nb_data)
    steps = int(math.ceil(nb_data / (1.0 * batch_size)))
    generator = Generator(data, steps=steps)

    cls_dict = {v: k for k, v in classes.items()}
    rel_dict = {v: k for k, v in relations.items()}
    
    with tf.device('/' + device):
        model = ELModel(nb_classes, nb_relations, embedding_size)
        optimizer = tf.train.GradientDescentOptimizer(learning_rate=0.01)
        loss_history = []
        for epoch in range(epochs):
            loss = 0.0
            for batch, batch_data in enumerate(generator):
                input, labels = batch_data
                with tf.GradientTape() as tape:
                    logits = model(input)
                    loss_value = tf.losses.mean_squared_error(labels, logits)
                    loss += loss_value.numpy()
                print(f'Batch loss {loss_value.numpy()}', end='\r', flush=True)    
                loss_history.append(loss_value.numpy())
                grads = tape.gradient(loss_value, model.variables)
                optimizer.apply_gradients(
                    zip(grads, model.variables),
                    global_step=tf.train.get_or_create_global_step())
            print(f'Epoch {epoch}: {loss / steps}')

            # Save embeddings every 10 epochs and at the end
            if epoch % 10 == 0 or epoch == epochs - 1:
                logging.info(f'Saving embeddings')
                cls_embeddings = model.cls_embeddings(
                    tf.range(nb_classes)).numpy()
                rel_embeddings = model.rel_embeddings(
                    tf.range(nb_relations)).numpy()
                cls_list = []
                rel_list = []
                for i in range(nb_classes):
                    cls_list.append(cls_dict[i])
                for i in range(nb_relations):
                    rel_list.append(rel_dict[i])

                df = pd.DataFrame(
                    {'classes': cls_list, 'embeddings': list(cls_embeddings)})
                df.to_pickle(out_classes_file)

                df = pd.DataFrame(
                    {'relations': rel_list, 'embeddings': list(rel_embeddings)})
                df.to_pickle(out_relations_file)


class ELModel(tf.keras.Model):

    def __init__(self, nb_classes, nb_relations, embedding_size):
        super(ELModel, self).__init__()
        self.nb_classes = nb_classes
        self.nb_relations = nb_relations
        
        self.cls_embeddings = tf.keras.layers.Embedding(
            nb_classes,
            embedding_size + 1,
            input_length=1)
        self.rel_embeddings = tf.keras.layers.Embedding(
            nb_relations,
            embedding_size,
            input_length=1)
            
    def call(self, input):
        """Run the model."""
        nf1, nf2, nf3, nf4, dis = input
        loss1 = self.nf1_loss(nf1)
        loss2 = self.nf2_loss(nf2)
        loss3 = self.nf3_loss(nf3)
        loss4 = self.nf4_loss(nf4)
        loss_dis = self.dis_loss(dis)
        loss = loss1 + loss2 + loss3 + loss4 + loss_dis
        return loss
   
    def loss(self, c, d):
        rc = tf.math.abs(c[:, -1])
        rd = tf.math.abs(d[:, -1])
        c = c[:, 0:-1]
        d = d[:, 0:-1]
        euc = tf.norm(c - d, axis=1)
        dst = tf.reshape(tf.nn.relu(euc + rc - rd), [-1, 1])
        # Regularization
        reg = tf.abs(tf.norm(c, axis=1) - 1) + tf.abs(tf.norm(d, axis=1) - 1)
        reg = tf.reshape(reg, [-1, 1])
        return dst + reg
    
    def nf1_loss(self, input):
        c = input[:, 0]
        d = input[:, 1]
        c = self.cls_embeddings(c)
        d = self.cls_embeddings(d)
        return self.loss(c, d)
    
    def nf2_loss(self, input):
        c = input[:, 0]
        d = input[:, 1]
        e = input[:, 2]
        c = self.cls_embeddings(c)
        d = self.cls_embeddings(d)
        e = self.cls_embeddings(e)
        rc = tf.reshape(tf.math.abs(c[:, -1]), [-1, 1])
        rd = tf.reshape(tf.math.abs(d[:, -1]), [-1, 1])
        re = tf.reshape(tf.math.abs(d[:, -1]), [-1, 1])
        sr = rc + rd
        x1 = c[:, 0:-1]
        x2 = d[:, 0:-1]
        x3 = e[:, 0:-1]
        x = x2 - x1
        dst = tf.reshape(tf.norm(x, axis=1), [-1, 1])
        dst2 = tf.reshape(tf.norm(x3 - x1, axis=1), [-1, 1])
        dst3 = tf.reshape(tf.norm(x3 - x2, axis=1), [-1, 1])
        rdst = tf.nn.relu(tf.math.maximum(rc, rd) - re)
        dst_loss = (tf.nn.relu(dst - sr)
                + tf.nn.relu(dst2 - rc)
                + tf.nn.relu(dst3 - rd)
                + rdst)
        reg = (tf.abs(tf.norm(x1, axis=1) - 1)
               + tf.abs(tf.norm(x2, axis=1) - 1)
               + tf.abs(tf.norm(x3, axis=1) - 1))
        reg = tf.reshape(reg, [-1, 1])
        return dst_loss + reg
        
                
    def nf3_loss(self, input):
        # C subClassOf R some D
        c = input[:, 0]
        r = input[:, 1]
        d = input[:, 2]
        c = self.cls_embeddings(c)
        d = self.cls_embeddings(d)
        r = self.rel_embeddings(r)
        r = tf.concat([r, tf.zeros((r.shape[0], 1), dtype=tf.float32)], 1)
        c = c + r
        return self.loss(c, d)

    def nf4_loss(self, input):
        # R some C subClassOf D
        r = input[:, 0]
        c = input[:, 1]
        d = input[:, 2]
        c = self.cls_embeddings(c)
        d = self.cls_embeddings(d)
        r = self.rel_embeddings(r)
        r = tf.concat([r, tf.zeros((r.shape[0], 1), dtype=tf.float32)], 1)
        c = c - r
        # c - r should intersect with d
        rc = tf.reshape(tf.math.abs(c[:, -1]), [-1, 1])
        rd = tf.reshape(tf.math.abs(d[:, -1]), [-1, 1])
        sr = rc + rd
        x1 = c[:, 0:-1]
        x2 = d[:, 0:-1]
        x = x2 - x1
        dst = tf.reshape(tf.norm(x, axis=1), [-1, 1])
        dst_loss = tf.nn.relu(dst - sr)
        reg = tf.abs(tf.norm(x1, axis=1) - 1) + tf.abs(tf.norm(x2, axis=1) - 1)
        reg = tf.reshape(reg, [-1, 1])
        return dst_loss + reg
    

    def dis_loss(self, input, margin=0.1):
        c = input[:, 0]
        d = input[:, 1]
        c = self.cls_embeddings(c)
        d = self.cls_embeddings(d)
        rc = tf.reshape(tf.math.abs(c[:, -1]), [-1, 1])
        rd = tf.reshape(tf.math.abs(d[:, -1]), [-1, 1])
        sr = rc + rd
        x1 = c[:, 0:-1]
        x2 = d[:, 0:-1]
        x = x2 - x1
        dst = tf.reshape(tf.norm(x, axis=1), [-1, 1])
        reg = tf.abs(tf.norm(x1, axis=1) - 1) + tf.abs(tf.norm(x2, axis=1) - 1)
        reg = tf.reshape(reg, [-1, 1])
        return tf.nn.relu(sr - dst - margin) + reg
        
        

class Generator(object):

    def __init__(self, data, batch_size=128, steps=100):
        self.data = data
        self.batch_size = batch_size
        self.steps = steps
        self.start = 0

    def __iter__(self):
        return self
    
    def __next__(self):
        return self.next()

    def reset(self):
        self.start = 0

    def next(self):
        if self.start < self.steps:
            nf1_index = np.random.choice(
                self.data['nf1'].shape[0], self.batch_size)
            nf2_index = np.random.choice(
                self.data['nf2'].shape[0], self.batch_size)
            nf3_index = np.random.choice(
                self.data['nf3'].shape[0], self.batch_size)
            nf4_index = np.random.choice(
                self.data['nf4'].shape[0], self.batch_size)
            dis_index = np.random.choice(
                self.data['disjoint'].shape[0], self.batch_size)
            nf1 = tf.convert_to_tensor(self.data['nf1'][nf1_index])
            nf2 = tf.convert_to_tensor(self.data['nf2'][nf2_index])
            nf3 = tf.convert_to_tensor(self.data['nf3'][nf3_index])
            nf4 = tf.convert_to_tensor(self.data['nf4'][nf4_index])
            dis = tf.convert_to_tensor(self.data['disjoint'][dis_index])
            labels = tf.zeros((self.batch_size, 1), dtype=tf.float32)
            self.start += 1
            return ((nf1, nf2, nf3, nf4, dis), labels)
        else:
            self.reset()
            raise StopIteration()


def load_data(filename):
    classes = {}
    relations = {}
    data = {'nf1': [], 'nf2': [], 'nf3': [], 'nf4': [], 'disjoint': []}
    with open(filename) as f:
        for line in f:
            # Ignore SubClassOf()
            line = line.strip()[11:-1]
            if not line:
                continue
            if line.startswith('ObjectIntersectionOf('):
                # C and D SubClassOf E
                it = line.split(' ')
                c = it[0][21:]
                d = it[1][:-1]
                e = it[2]
                if c not in classes:
                    classes[c] = len(classes)
                if d not in classes:
                    classes[d] = len(classes)
                if e not in classes:
                    classes[e] = len(classes)
                if e == 'owl:Nothing':
                    data['disjoint'].append((classes[c], classes[d], classes[e]))
                else:
                    data['nf2'].append((classes[c], classes[d], classes[e]))
            elif line.startswith('ObjectSomeValuesFrom('):
                # R some C SubClassOf D
                it = line.split(' ')
                r = it[0][21:]
                c = it[1][:-1]
                d = it[2]
                if c not in classes:
                    classes[c] = len(classes)
                if d not in classes:
                    classes[d] = len(classes)
                if r not in relations:
                    relations[r] = len(relations)
                data['nf4'].append((relations[r], classes[c], classes[d]))
            elif line.find('ObjectSomeValuesFrom') != -1:
                # C SubClassOf R some D
                it = line.split(' ')
                c = it[0]
                r = it[1][21:]
                d = it[2][:-1]
                if c not in classes:
                    classes[c] = len(classes)
                if d not in classes:
                    classes[d] = len(classes)
                if r not in relations:
                    relations[r] = len(relations)
                data['nf3'].append((classes[c], relations[r], classes[d]))
            else:
                # C SubClassOf D
                it = line.split(' ')
                c = it[0]
                d = it[1]
                if c not in classes:
                    classes[c] = len(classes)
                if d not in classes:
                    classes[d] = len(classes)
                data['nf1'].append(
                    (classes[c], classes[d]))
                
    data['nf1'] = np.array(data['nf1'])
    data['nf2'] = np.array(data['nf2'])
    data['nf3'] = np.array(data['nf3'])
    data['nf4'] = np.array(data['nf4'])
    data['disjoint'] = np.array(data['disjoint'])
    return data, classes, relations

if __name__ == '__main__':
    main()
