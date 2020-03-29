import random
import copy
import string
import warnings
from pathlib import Path
import pandas as pd

from processing.embeddings import load_ft_model
from processing.tfidf import tfidf
from processing.importer import load_tarfile

# nltk.download('stopwords')
CONLL_COLS = ['form', 'lemma', 'upos', 'xpos', 'feats', 'head', 'deprel']


class Corpus:
    """
    *** UPDATE DOCSTRING ***
    """

    def __init__(self, doc_series, indexed=False):
        self.doc_series = doc_series.copy()
        self.df = doc_series.to_frame().rename({'Word': 'token'}, axis='columns')

        self.doc_ids = set(self.doc_series.index.unique('doc'))
        self.n_docs = len(self.doc_series.index.unique('doc'))

        if not indexed:
            self._idx_sentences()
        # else:
        #     self.doc_series = self._idx_doc_series()

    @classmethod
    def from_tarfile(cls, path, word_col='token'):
        return cls(load_tarfile(path)[word_col])

    @classmethod
    def from_parquet(cls, path, word_col='token'):
        df = pd.read_parquet(path)
        return cls.from_frame(df, word_col, indexed=False)

    @classmethod
    def from_frame(cls, df, word_col, indexed=True):

        corpus = cls(df[word_col], indexed=indexed)
        corpus.df = df
        return corpus

    def copy(self):
        return copy.deepcopy(self)

    def downsample(self, n=100):
        selected = random.sample(self.doc_ids, n)
        self.doc_series.index = self.df.index

        # sample_idx = pd.MultiIndex.from_frame(
        #     self.df[slice(None)].loc[(selected, slice(None), slice(None)), :]
        #         .reset_index(drop=True)#['doc', 'sent', 'word']
        # )

        sample_idx = self.df.index.to_frame().loc[(selected, slice(None), slice(None))].index#.drop('doc').set_index('doc', 'sent', 'word') \
                         #.loc[(selected, slice(None), slice(None)), :].index

        self.df = self.df.loc[sample_idx, :]
        self.doc_series = self.doc_series[sample_idx]
        self.doc_ids = selected
        self.n_docs = len(self.doc_ids)

    def process(self, *args):

        methods = []
        for i, arg in enumerate(args):
            methods += [name for name in dir(self) if arg in name]
        for method in methods:
            getattr(Corpus, method)(self)

        print(f'Added columns {methods}')

    def zip_sents(self):
        docs = self.doc_series
        docs.index = self.df.index
        return docs.groupby(['doc', 'sent']).apply(list).str.join(' ')

    def add_locators(self): # probably not needed
        # self.df['doc_loc'] = self.df.groupby('doc').cumcount() + 1
        self.df['sent_id'] = self.df.reset_index()['sent'].values
        self.df['sent_loc'] = self.df.reset_index()['word'].values
        return self

    def add_stem(self):
        import nltk
        stemmer = nltk.stem.snowball.SnowballStemmer(language='english')
        self.df['stem'] = [stemmer.stem(token) for token in list(self.doc_series)]
        return self

    def add_pos(self):
        from nltk import pos_tag
        # not used, included in CoNNL
        self.df['nltkpos'] = [tag for char, tag in pos_tag(self.doc_series.values.flatten().tolist(), lang='eng')]
        return self

    def add_pos_stem(self):
        if 'xpos' in self.df.columns:
            self.df['xpos_stem'] = (self.df['xpos']).str.slice(0, 2)
        elif 'nltkpos' in self.df.columns:
            self.df['nltkpos'] = (self.df['nltkpos']).str.slice(0, 2)
        else:
            raise Exception('No POS column found in data.')

    def add_tfidf(self):

        self.tfidf_ = tfidf(self.zip_sents().values.tolist())
        # print(self.tfidf_)
        # self.tfidf_tab = pd.DataFrame(self.tfidf_).iloc[:, 0].sort()

        # print(self.tfidf_tab)
        scores = self.doc_series.map(self.tfidf_).fillna(0.0)

        self.df['tfidf'] = scores
        self.df['sent_tfidf'] = scores.groupby(['doc', 'sent']).sum()
        return self

    def lag_cols(self, columns, window_size, level, fill_value='<NONE>'):
        lags = list(range(-window_size, window_size + 1))
        lags.remove(0)

        for column in columns:
            for lag in lags:
                self.df[f'{column.upper()}_LAG{lag}'] = self.df[column].groupby(level).shift(lag).fillna(fill_value)
        return self

    def mark_stopwords(self):
        from nltk.corpus import stopwords
        stop_words = set(stopwords.words('english')) - {'no', 'not', 'nor'}
        self.df['stopword'] = self.doc_series.isin(stop_words).values
        return self

    def mark_punctuation(self):
        punctuation = set(string.punctuation)
        self.df['punctuation'] = self.doc_series.isin(punctuation).values
        return self

    def mark_capitals(self):
        self.df['is_upper'] = [token.isupper() for token in self.doc_series.values]
        self.df['is_lower'] = [token.islower() for token in self.doc_series.values]
        self.df['is_title'] = [token.istitle() for token in self.doc_series.values]

        if 'sent' in self.df.index.names:
            self.df['near_cap'] = self.df['is_upper'].groupby(['doc', 'sent']).sum() >= 1

        return self

    def mark_numeric(self):

        # print(self.doc_series.isna().sum())
        # print(self.doc_series[self.doc_series is None])

        self.df['is_int'] = self.doc_series.str.isdigit()
        self.df['is_dec'] = self.doc_series.apply(self.mark_decimal)
        return self

    @staticmethod
    def mark_decimal(token):
        import decimal
        try:
            decimal.Decimal(token)
            if token.isdigit(): raise decimal.InvalidOperation
            return True
        except decimal.InvalidOperation:
            return False
        except TypeError:
            print(token)

    def mark_first_last(self):

        print(self.df)
        if 'sent_id' not in self.df.columns:
            raise Exception('Add locator columns first.')

        n_sents_doc = self.df.groupby(['doc'])['sent_id'].transform(max)
        n_words_sent = self.df.groupby(['doc', 'sent'])['sent_loc'].transform(max)

        self.df['first_sent'] = self.df['sent_id'] == 1
        self.df['last_sent'] = self.df['sent_id'] == n_sents_doc
        self.df['first_word'] = self.df['sent_loc'] == 1
        self.df['last_word'] = self.df['sent_loc'] == n_words_sent
        return self

    def load_embeddings(self, filepath='data\\processing\\ft_embeds.parquet',
                        model_path='models/ft_models/BioWordVec_PubMed_MIMICIII_d200.bin'):

        if Path(filepath).exists():
            embeddings = pd.read_parquet(str(filepath))
            print(f'Loading FastText embeddings from {filepath}.')

        else:
            print(f'No embeddings found at {filepath}. Loading model for vector query. This may take a long time.')

            model = load_ft_model(model_path)
            embeddings = pd.DataFrame([model.get_word_vector(word) for word in self.doc_series.values],
                                      index=self.df.index, columns=[f'PMFT_{i + 1}' for i in range(200)])
            embeddings.to_parquet(str(filepath))

        # embeddings = embeddings.loc[self.df.index]
        # self.df = pd.concat([c.reset_index(drop=True) for c in [self.df, embeddings]], axis=1).set_index(self.df.index)
        self.df = self.df.join(embeddings)
        return self

    def load_CoNLL(self, filepath='data\\processing\\conll.csv'):
        from processing.conll_parse import conll_parse

        if not Path(filepath).exists():
            print(f'No CoNNL output detected at {filepath}. Generating data.')
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                conll_parse(self.zip_sents(), filepath)

        print(f'Loading CoNLL data from {filepath}.')

        index = self.df.index
        output = pd.read_csv(filepath, sep='\t', header=None, keep_default_na=False).iloc[:, 2:8].dropna(axis=0, how='all')
        output.columns = CONLL_COLS # todo: add cols

        # self.df = pd.concat([c.reset_index(drop=True) for c in [self.df, output]], axis=1)
        # self.df.index = index

        # output = pd.read_parquet(filepath).iloc[:, 2:8]

        nan = output.isna().sum().sum()
        if nan > 1:
            print(f'Warning: {nan} missing values detected in CoNNL columns.')

        self.df = self.df.join(output.fillna('_'))

        return self

    def parse_deprel(self, parse_features):
        self.df['dist_to_parent'] = ~(self.df['head'] == 0) * abs(self.df['sent_loc'] - self.df['head'])

        head_orient = ~(self.df['head'] == 0) * (self.df['head'] - self.df['sent_loc'])
        head_loc = pd.Index((self.df.reset_index(drop=True).index + head_orient).reset_index(drop=True))

        parents = self.df.reset_index().loc[head_loc]
        parents[self.df.reset_index().index == head_loc] = '_'
        parents.columns = ['par_' + str(col) for col in parents.columns]

        self.df = pd.concat([self.df.reset_index(drop=True), parents[parse_features].reset_index(drop=True)], axis=1) \
            .set_index(self.df.index)

        # print(self.df)
        # print(parents)
        # print(self.df.index)
        # print(parents.index)
        #
        # self.df = self.df.join(parents)

        return self

    def load_sentiments(self, filepath='data\\processing\\sentiments.parquet'):

        if Path(filepath).exists():
            print(f'Loading sentiments from {filepath}')
            self.df[['polarity', 'subjectivity']] = pd.read_parquet(filepath)

        else:
            from textblob import TextBlob
            print(f'Running TextBlob sentiment analysis over {len(self.df)} instances.')

            self.df[['polarity', 'subjectivity']] = self.zip_sents() \
                .apply(lambda x: pd.Series(TextBlob(x).sentiment))

        return self

    def save(self, path):
        ext = str(path).split('.')[-1]
        if ext == 'parquet':
            self.df.to_parquet(path)
        elif ext == 'pickle':
            self.df.to_pickle(path)
        elif ext == 'csv':
            self.df.to_csv(path)
        else:
            print('Invalid file extension specified. Supported formats: .pickle, .parquet, .csv (save only)')

    def load_df(self, path):
        ext = str(path).split('.')[-1]
        if ext == 'parquet':
            self.df = self.df.read_parquet(path)
        elif ext == 'pickle':
            self.df = self.df.read_pickle(path)
        else:
            print('Invalid file extension specified. Supported formats: .pickle, .parquet')

    def _idx_sentences(self):
        print(f'Indexing sentences for {self.n_docs} documents.')
        new_indices = self.doc_series.copy().to_frame()

        sent_break = new_indices.mask(new_indices == '.', 1).mask(new_indices != '.', 0)

        sent_id = sent_break.groupby('doc').shift(1).fillna(method='bfill') \
                            .groupby('doc').expanding().sum().astype(int).values

        new_indices['sent_idx'] = sent_id + 1

        new_indices['word_idx'] = (new_indices.set_index('sent_idx',append=True)
                                              .groupby(['doc', 'sent_idx']).cumcount() + 1).values

        self.df.index = pd.MultiIndex.from_frame(
            new_indices.reset_index().drop('idx', axis=1)[['doc', 'sent_idx', 'word_idx']],
            names=['doc', 'sent', 'word']
        )

        self.doc_series.index = self.df.index

    # def _idx_doc_series(self):
    #     self.doc_series = self.df['token'].reset_index(['sent', 'word'], drop=True) \
    #                          .set_index(self.df.index.to_frame().iloc[:, -1]
    #                                            .groupby('doc').cumcount(), append=True)
    #     return self


if __name__ == '__main__':
    testfile = 'data/split/train.parquet'
    data = pd.read_parquet(testfile)['form']
    corpus = Corpus(data)
    print(corpus.df.reset_index()[corpus.doc_series.isna().reset_index(drop=True)])

    # print(corpus.doc_series[corpus.doc_series==None])
    corpus.mark_numeric()
    corpus.calc_tfidf()
    corpus.mark_capitals()
    corpus.load_sentiments()

    print(corpus.df.index.names)
    print(corpus.df)
    print(corpus.df.sum())

    corpus.df['form'] = corpus.doc_series

    corpus.df['Word'] = corpus.df.reset_index()['token'].tolist()
    corpus.save('test.parquet')

    print(pd.read_parquet('test.parquet')['Word'])

    corpus2 = corpus.from_frame(corpus.df, word_col='Word', indexed=True)
    corpus3 = corpus.from_parquet('test.parquet')

    print(corpus3.df['near_cap'])

    corpus3.save('test.csv')
