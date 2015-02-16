from collections import defaultdict
import cPickle
import os
from itertools import groupby

import jieba
import numpy as np
import scipy.sparse as sp

from base import *


__all__ = ['GroceryTextConverter']


def _dict2list(d):
    if len(d) == 0:
        return []
    m = max(v for k, v in d.iteritems())
    ret = [''] * (m + 1)
    for k, v in d.iteritems():
        ret[v] = k
    return ret


def _list2dict(l):
    return dict((v, k) for k, v in enumerate(l))


class GroceryTextPreProcessor(object):
    def __init__(self):
        # index must start from 1
        self.tok2idx = {'>>dummy<<': 0}
        self.idx2tok = None

    @staticmethod
    def _default_tokenize(text):
        return jieba.cut(text, cut_all=True)

    def preprocess(self, text, custom_tokenize):
        if custom_tokenize is not None:
            tokens = custom_tokenize(text)
        else:
            tokens = self._default_tokenize(text)
        ret = []
        for idx, tok in enumerate(tokens):
            if tok not in self.tok2idx:
                self.tok2idx[tok] = len(self.tok2idx)
            ret.append(self.tok2idx[tok])
        return ret

    def save(self, dest_file):
        self.idx2tok = _dict2list(self.tok2idx)
        config = {'idx2tok': self.idx2tok}
        cPickle.dump(config, open(dest_file, 'wb'), -1)

    def load(self, src_file):
        config = cPickle.load(open(src_file, 'rb'))
        self.idx2tok = config['idx2tok']
        self.tok2idx = _list2dict(self.idx2tok)
        return self


class GroceryFeatureGenerator(object):
    def __init__(self):
        self.ngram2fidx = {'>>dummy<<': 0}
        self.fidx2ngram = None

    def unigram(self, tokens):
        feat = defaultdict(int)
        NG = self.ngram2fidx
        for x in tokens:
            if (x,) not in NG:
                NG[x,] = len(NG)
            feat[NG[x,]] += 1
        return feat

    def bigram(self, tokens):
        feat = self.unigram(tokens)
        NG = self.ngram2fidx
        for x, y in zip(tokens[:-1], tokens[1:]):
            if (x, y) not in NG:
                NG[x, y] = len(NG)
            feat[NG[x, y]] += 1
        return feat

    def save(self, dest_file):
        self.fidx2ngram = _dict2list(self.ngram2fidx)
        config = {'fidx2ngram': self.fidx2ngram}
        cPickle.dump(config, open(dest_file, 'wb'), -1)

    def load(self, src_file):
        config = cPickle.load(open(src_file, 'rb'))
        self.fidx2ngram = config['fidx2ngram']
        self.ngram2fidx = _list2dict(self.fidx2ngram)
        return self


class GroceryClassMapping(object):
    def __init__(self):
        self.class2idx = {}
        self.idx2class = None

    def to_idx(self, class_name):
        if class_name in self.class2idx:
            return self.class2idx[class_name]

        m = len(self.class2idx)
        self.class2idx[class_name] = m
        return m

    def to_class_name(self, idx):
        if self.idx2class is None:
            self.idx2class = _dict2list(self.class2idx)
        if idx == -1:
            return "**not in training**"
        if idx >= len(self.idx2class):
            raise KeyError(
                'class idx ({0}) should be less than the number of classes ({0}).'.format(idx, len(self.idx2class)))
        return self.idx2class[idx]

    def save(self, dest_file):
        self.idx2class = _dict2list(self.class2idx)
        config = {'idx2class': self.idx2class}
        cPickle.dump(config, open(dest_file, 'wb'), -1)

    def load(self, src_file):
        config = cPickle.load(open(src_file, 'rb'))
        self.idx2class = config['idx2class']
        self.class2idx = _list2dict(self.idx2class)
        return self


class FakeSparse(object):
    def __init__(self, data=None, indices=None, indptr=None, shape=None):
        self.data = data
        self.indices = indices
        self.indptr = indptr
        self.shape = shape


class GroceryTextConverter(object):
    def __init__(self, custom_tokenize=None):
        self.text_prep = GroceryTextPreProcessor()
        self.feat_gen = GroceryFeatureGenerator()
        self.class_map = GroceryClassMapping()
        self.custom_tokenize = custom_tokenize

    def get_class_idx(self, class_name):
        return self.class_map.to_idx(class_name)

    def get_class_name(self, class_idx):
        return self.class_map.to_class_name(class_idx)

    def to_svm(self, text, class_name=None):
        feat = self.feat_gen.bigram(self.text_prep.preprocess(text, self.custom_tokenize))
        if class_name is None:
            return feat
        return feat, self.class_map.to_idx(class_name)

    def _sort_features(self, X, vocabulary):
        sorted_features = sorted(vocabulary.iteritems())
        map_index = np.empty(len(sorted_features), dtype=np.int32)
        for new_val, (term, old_val) in enumerate(sorted_features):
            map_index[new_val] = old_val
            vocabulary[term] = new_val
        return X[:, map_index]

    def convert_text(self, text_src, delimiter):
        def accumulate(iterator):
            total = 0
            for item in iterator:
                total += item
                yield total

        def _np(lst):
            return np.asarray(lst)

        text_src = read_text_src(text_src, delimiter)
        indptr = [0]
        raw_sparse = []
        labels = []
        for idx, line in enumerate(text_src):
            try:
                label, text = line
            except ValueError:
                continue
            feat, label = self.to_svm(text, label)
            labels.append(label)
            raw_sparse.extend([(idx, f, feat[f]) for f in feat])
        raw_sparse = sorted(raw_sparse, key=lambda x: x[1])
        indices, f, values = zip(*raw_sparse)
        indptr.extend(accumulate([len(list(g)) for k, g in groupby(f)]))
        # return FakeSparse(_np(values), _np(indices), _np(indptr), (len(labels), len(indptr) - 1)), labels
        X = sp.csr_matrix((values, indices, indptr), shape=(len(labels), len(indptr) - 1))
        X = self._sort_features(X, self.feat_gen.ngram2fidx)
        return X, labels

    def save(self, dest_dir):
        config = {
            'text_prep': 'text_prep.config.pickle',
            'feat_gen': 'feat_gen.config.pickle',
            'class_map': 'class_map.config.pickle',
        }
        if not os.path.exists(dest_dir):
            os.mkdir(dest_dir)
        self.text_prep.save(os.path.join(dest_dir, config['text_prep']))
        self.feat_gen.save(os.path.join(dest_dir, config['feat_gen']))
        self.class_map.save(os.path.join(dest_dir, config['class_map']))

    def load(self, src_dir):
        config = {
            'text_prep': 'text_prep.config.pickle',
            'feat_gen': 'feat_gen.config.pickle',
            'class_map': 'class_map.config.pickle',
        }
        self.text_prep.load(os.path.join(src_dir, config['text_prep']))
        self.feat_gen.load(os.path.join(src_dir, config['feat_gen']))
        self.class_map.load(os.path.join(src_dir, config['class_map']))
        return self
