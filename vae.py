import numpy as np
import _dynet as dy

class Seq2SeqBase:
    def to_sequence_batch(self, decoding, out_vocab):
        batch_size = decoding[0].dim()[1]
        decoding = [ dy.softmax(x) for x in decoding ]
        decoding = [ dy.reshape(x, (len(out_vocab), batch_size), batch_size=1) for x in decoding ]
        decoding = [ np.argmax(x.value(), axis=0) for x in decoding ]
        decoding = [  [ x[i] for x in decoding ] for i in range(0, batch_size) ]
        return [ [ out_vocab[y] for y in x ] for x in decoding ]

    def one_batch(self, X_batch, y_batch, X_masks, y_masks, eos=133, training=True):
        batch_size = len(X_batch)
        X_batch = zip(*X_batch)
        X_masks = zip(*X_masks)
        y_batch = zip(*y_batch)
        y_masks = zip(*y_masks)

        if training:
            decoding = self.backward_batch(X_batch, y_batch, X_masks, eos)
        else:
            decoding = self.forward_batch(X_batch, len(y_batch), X_masks, eos)

        batch_loss = []
        for x, y, mask in zip(decoding, y_batch, y_masks):
            mask_expr = dy.inputVector(mask)
            mask = dy.reshape(mask_expr, (1,), batch_size)
            batch_loss.append(mask * dy.pickneglogsoftmax_batch(x, y))
        batch_loss = dy.esum(batch_loss)
        batch_loss = dy.sum_batches(batch_loss)

        #needs to be averaged...
        return batch_loss, decoding

class Seq2SeqVanilla(Seq2SeqBase):
    def __init__(self, collection, vocab_size, out_vocab_size, input_embedding_dim=512, output_embedding_dim=32, \
            encoder_layers=2, decoder_layers=2, encoder_hidden_dim=512, decoder_hidden_dim=512, \
            encoder_dropout=0.5, decoder_dropout=0.5):
        self.collection = collection
        self.params = {}

        self.params['W_emb'] = collection.add_lookup_parameters((vocab_size, input_embedding_dim))
        self.params['Wout_emb'] = collection.add_lookup_parameters((out_vocab_size, output_embedding_dim))

        self.encoder = dy.VanillaLSTMBuilder(encoder_layers, input_embedding_dim, encoder_hidden_dim, collection)
        self.decoder = dy.VanillaLSTMBuilder(decoder_layers, output_embedding_dim, decoder_hidden_dim, collection)

        self.params['R'] = collection.add_parameters((out_vocab_size, decoder_hidden_dim)) 
        self.params['b'] = collection.add_parameters((out_vocab_size,)) 

        self.layers = encoder_layers
        self.encoder_dropout = encoder_dropout
        self.decoder_dropout = decoder_dropout

    def get_params(self):
        W_emb = self.params['W_emb']
        Wout_emb = self.params['Wout_emb']
        R = dy.parameter(self.params['R'])
        b = dy.parameter(self.params['b'])
        return W_emb, Wout_emb, R, b

    def encode(self, W_emb, X_batch, X_masks):
        X = [ dy.lookup_batch(W_emb, tok_batch) for tok_batch in X_batch ]

        s0 = self.encoder.initial_state()
        states = s0.add_inputs(X)

        encoding = [ state.h()[-1] for state in states ]

        final = states[-1].s()

        for i, mask in enumerate(X_masks):
            mask_expr = dy.inputVector(mask)
            mask = dy.reshape(mask_expr, (1,), len(mask))
            encoding[i] = encoding[i] * mask
        encoding = dy.concatenate_cols(encoding)

        return encoding, final

    def backward_batch(self, X_batch, y_batch, X_masks, eos):
        W_emb, Wout_emb, R, b = self.get_params()

        self.encoder.set_dropouts(self.encoder_dropout, 0)
        self.decoder.set_dropouts(self.decoder_dropout, 0)

        encoding, final = self.encode(W_emb, X_batch, X_masks)
        s0 = self.decoder.initial_state(vecs=final)

        #teacher forcing
        eoses = dy.lookup_batch(Wout_emb, [ eos ] * len(X_batch[0]))
        tf = [ eoses ] + [ dy.lookup_batch(Wout_emb, y) for y in y_batch ]

        history = s0.transduce(tf)         #transduce lower layers
        decoding = [ dy.affine_transform([b, R, h_i]) for h_i in history ]

        return decoding

    def forward_batch(self, X_batch, maxlen, X_masks, eos):
        W_emb, Wout_emb, R, b = self.get_params()

        self.encoder.set_dropouts(0, 0)
        self.decoder.set_dropouts(0, 0)

        encoding, final = self.encode(W_emb, X_batch, X_masks)
        s0 = self.decoder.initial_state(vecs=final)

        #eos to start the sequence
        eoses = dy.lookup_batch(Wout_emb, [ eos ] * len(X_batch[0]))
        state = s0.add_input(eoses)

        decoding = []
        for i in range(0, maxlen):
            h_i = state.h()[-1]

            #probability dist
            decoding.append(dy.affine_transform([b, R, h_i]))

            #beam size 1
            probs = dy.softmax(decoding[-1])
            dim = probs.dim()
            flatten = dy.reshape(probs, (dim[0][0], dim[1]), batch_size=1)
            beam = np.argmax(flatten.value(), axis=0)
            state = state.add_input(dy.lookup_batch(Wout_emb, beam))
        return decoding
