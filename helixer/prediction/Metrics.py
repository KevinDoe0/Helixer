import numpy as np
import os
import csv
from collections import defaultdict
from terminaltables import AsciiTable
from scipy.sparse import coo_matrix


class ConfusionMatrix():

    def __init__(self, col_names=['ig', 'utr', 'exon', 'intron']):
        self.col_names = {i:name for i, name in enumerate(col_names)}
        self.n_classes = len(self.col_names)
        self.cm = np.zeros((self.n_classes, self.n_classes), dtype=np.uint64)
        self.uncertainties = {col_name:list() for col_name in self.col_names.values()}
        self.max_uncertainty = -sum([e * np.log2(e) for e in [1 / self.n_classes] * self.n_classes])

    @staticmethod
    def _argmax_y(arr):
        arr = np.argmax(arr, axis=-1).ravel().astype(np.int8)
        return arr

    @staticmethod
    def _remove_masked_bases(y_true, y_pred, sw):
        """Remove bases marked as errors, should also remove zero padding"""
        sw = sw.astype(np.bool)
        y_true = y_true[sw]
        y_pred = y_pred[sw]
        return y_true, y_pred

    def _add_to_cm(self, y_true, y_pred):
        """Put in extra function to be testable"""
        # add to confusion matrix as long as _some_ bases were not masked
        if y_pred.size > 0:
            y_pred = ConfusionMatrix._argmax_y(y_pred)
            y_true = ConfusionMatrix._argmax_y(y_true)
            # taken from here, without the boiler plate:
            # https://github.com/scikit-learn/scikit-learn/blob/
            # 42aff4e2edd8e8887478f6ff1628f27de97be6a3/sklearn/metrics/_classification.py#L342
            cm_batch = coo_matrix((np.ones(y_true.shape[0], dtype=np.int8), (y_true, y_pred)),
                                   shape=(4, 4), dtype=np.uint32).toarray()
            self.cm += cm_batch

    def _add_to_uncertainty(self, y_true, y_pred):
        y_pred = y_pred.reshape((-1, y_pred.shape[-1]))
        y_true = ConfusionMatrix._argmax_y(y_true)
        # entropy calculation
        y_pred_log2 = np.log2(y_pred)
        y_pred_H = -1 * np.sum(y_pred * y_pred_log2, axis=-1)
        # average entropy for all the bases in one class according to the labels
        for i, name in self.col_names.items():
            class_mask = (y_true == i)
            if np.any(class_mask):
                avg_entropy = np.nanmean(y_pred_H[class_mask])
                avg_entropy /= self.max_uncertainty  # normalize by maximum for comparability
                self.uncertainties[name].append(avg_entropy)

    def count_and_calculate_one_batch(self, y_true, y_pred, sw):
        y_true, y_pred = ConfusionMatrix._remove_masked_bases(y_true, y_pred, sw)
        # important to copy so _add_to_cm() works
        self._add_to_uncertainty(np.copy(y_true), np.copy(y_pred))
        self._add_to_cm(y_true, y_pred)

    def _get_normalized_cm(self):
        """Put in extra function to be testable"""
        class_sums = np.sum(self.cm, axis=1)
        normalized_cm = self.cm / class_sums[:, None]  # expand by one dim so broadcast work properly
        return normalized_cm

    @staticmethod
    def _precision_recall_f1(tp, fp, fn):
        if (tp + fp) > 0:
            precision = tp / (tp + fp)
        else:
            precision = 0.0  # avoid an error due to division by 0
        if (tp + fn) > 0:
            recall = tp / (tp + fn)
        else:
            recall = 0.0
        if (precision + recall) > 0:
            f1 = 2 * precision * recall / (precision + recall)
        else:
            f1 = 0.0
        return precision, recall, f1

    def _total_accuracy(self):
        return np.trace(self.cm) / np.sum(self.cm)

    @staticmethod
    def _add_to_scores(d):
        metrics = ConfusionMatrix._precision_recall_f1(d['TP'], d['FP'], d['FN'])
        d['precision'], d['recall'], d['f1'] = metrics

    def _get_scores(self):
        scores = defaultdict(dict)
        # single column metrics
        for col in range(4):
            name = self.col_names[col]
            d = scores[name]
            not_col = np.arange(4) != col
            d['TP'] = self.cm[col, col]
            d['FP'] = np.sum(self.cm[not_col, col])
            d['FN'] = np.sum(self.cm[col, not_col])
            # add average uncertanties
            d['H'] = np.mean(self.uncertainties[name])

            ConfusionMatrix._add_to_scores(d)
        return scores

    def _print_results(self, scores):
        for table, table_name in self.prep_tables(scores):
            print('\n', AsciiTable(table, table_name).table, sep='')
        print('Total acc: {:.4f}'.format(ConfusionMatrix._total_accuracy()))

    def print_cm(self):
        scores = self._get_scores()
        self._print_results(scores)

    def prep_tables(self, scores):
        out = []
        names = list(self.col_names.values())

        # confusion matrix
        cm = [[''] + [x + '_pred' for x in names]]
        for i, row in enumerate(self.cm.astype(int).tolist()):
            cm.append([names[i] + '_ref'] + row)
        out.append((cm, 'confusion_matrix'))

        # normalized
        normalized_cm = [cm[0]]
        for i, row in enumerate(self._get_normalized_cm().tolist()):
            normalized_cm.append([names[i] + '_ref'] + [round(x, ndigits=4) for x in row])
        out.append((normalized_cm, 'normalized_confusion_matrix'))

        # F1
        table = [['', 'norm. H', 'Precision', 'Recall', 'F1-Score']]
        for i, (name, values) in enumerate(scores.items()):
            # check if there is an entropy value comming (only for single type classes)
            if i < len(names):
                metrics = []
            else:
                metrics = ['']
            metrics += ['{:.4f}'.format(s) for s in list(values.values())[3:]]  # [3:] to skip TP, FP, FN
            table.append([name] + metrics)
            if i == (len(names) - 1) and len(scores > len(names)):
                table.append([''] * 4)
        out.append((table, 'F1_summary'))

        return out

    def export_to_csvs(self, pathout):
        if pathout is not None:
            if not os.path.exists(pathout):
                os.mkdir(pathout)

            for table, table_name in self.prep_tables():
                with open('{}/{}.csv'.format(pathout, table_name), 'w') as f:
                    writer = csv.writer(f)
                    for row in table:
                        writer.writerow(row)


class ConfusionMatrixGenic(ConfusionMatrix):
    """Extension of ConfusionMatrix that just adds the calculation of the composite scores"""

    def _get_scores(self):
        scores = super()._get_scores()

        # legacy cds score that works the same as the cds_f1 with the 3 column multi class encoding
        # essentiall merging the predictions as if error between exon and intron did not matter
        d = scores['legacy_cds']
        cm = self.cm
        d['TP'] = cm[2, 2] + cm[3, 3] + cm[2, 3] + cm[3, 2]
        d['FP'] = cm[0, 2] + cm[0, 3] + cm[1, 2] + cm[1, 3]
        d['FN'] = cm[2, 0] + cm[3, 0] + cm[2, 1] + cm[3, 1]
        ConfusionMatrix._add_to_scores(d)

        # subgenic metric is essentially the same as the genic one
        # pretty redundant code to below, but done for minimizing the risk to mess up (for now)
        d = scores['sub_genic']
        for base_metric in ['TP', 'FP', 'FN']:
            d[base_metric] = sum([scores[m][base_metric] for m in ['exon', 'intron']])
        ConfusionMatrix._add_to_scores(d)

        # genic metrics are calculated by summing up TP, FP, FN, essentially calculating a weighted
        # sum for the individual metrics. TP of the intergenic class are not taken into account
        d = scores['genic']
        for base_metric in ['TP', 'FP', 'FN']:
            d[base_metric] = sum([scores[m][base_metric] for m in ['utr', 'exon', 'intron']])
        ConfusionMatrix._add_to_scores(d)

        return scores


class Metrics():

    def __init__(self, generator, print_to_stdout=True):
        np.set_printoptions(suppress=True)  # do not use scientific notation for the print out
        self.generator = generator
        self.print_to_stdout = print_to_stdout
        self.cm_genic = ConfusionMatrixGenic()
        self.cm_phase = ConfusionMatrix(['no phase', 'phase 0', 'phase 1', 'phase 2'])

    def calculate_metrics(self, model):
        for batch_idx in range(len(self.generator)):
            print(batch_idx, '/', len(self.generator) - 1, end="\r")

            y_pred_phase = None
            inputs = self.generator[batch_idx]
            if len(inputs) == 2 and type(inputs[0]) is list:
                # dilated conv input scheme
                (X, sw), y_true = inputs
                y_pred = model.predict_on_batch([X, sw])
            elif len(inputs) == 3:
                if type(inputs[0]) is list:
                    # correction model input scheme
                    (X, pred), y_true, sw = inputs
                    y_pred = model.predict_on_batch([X, pred])
                elif type(inputs[1]) is list:
                    # phase prediction input scheme
                    X, (y_true, y_true_phase), sw = inputs
                    y_pred, y_pred_phase = model.predict_on_batch(X)
                else:
                    X, y_true, sw = inputs
                    y_pred = model.predict_on_batch(X)
            else:
                print('Unknown inputs from keras sequence')
                exit()

            data = {'genic_base_wise': [self.cm_genic, (y_true, y_pred)]}
            if y_pred_phase:
                data['phase_base_wise'] = [self.cm_phase, (y_true_phase, y_pred_phase)]

            all_scores = {}
            for metric_name, (cm, (y_true, y_pred)) in data.items():
                if self.generator.overlap:
                    assert len(y_pred.shape) == 4, "this reshape assumes shape is " \
                                                   "(batch_size, chunk_size // pool, pool, label dim)" \
                                                   "and apparently it is time to fix that, shape is {}".format(y_pred.shape)
                    bs, cspool, pool, ydim = y_pred.shape
                    y_pred = y_pred.reshape([bs, cspool * pool, ydim])
                    y_pred = self.generator.ol_helper.overlap_predictions(batch_idx, y_pred)
                    y_pred = y_pred.reshape([-1, cspool, pool, ydim])
                    # edge handle sw & y_true (as done with y_pred and to restore 1:1 input output
                    sw = self.generator.ol_helper.subset_input(batch_idx, sw)
                    y_true = self.generator.ol_helper.subset_input(batch_idx, y_true)
                cm.count_and_calculate_one_batch(y_true, y_pred, sw)

                scores = cm._get_scores()
                if cm.print_to_stdout:
                    cm._print_results(scores)
                all_scores[metric_name] = scores
        return all_scores

