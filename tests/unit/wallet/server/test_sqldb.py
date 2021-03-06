import unittest
from binascii import hexlify
from torba.client.constants import COIN, NULL_HASH32

from lbrynet.schema.claim import Claim
from lbrynet.wallet.server.db import SQLDB
from lbrynet.wallet.server.trending import TRENDING_WINDOW
from lbrynet.wallet.server.block_processor import Timer
from lbrynet.wallet.transaction import Transaction, Input, Output


def get_output(amount=COIN, pubkey_hash=NULL_HASH32):
    return Transaction() \
        .add_outputs([Output.pay_pubkey_hash(amount, pubkey_hash)]) \
        .outputs[0]


def get_input():
    return Input.spend(get_output())


def get_tx():
    return Transaction().add_inputs([get_input()])


def claim_id(claim_hash):
    return hexlify(claim_hash[::-1]).decode()


class OldWalletServerTransaction:
    def __init__(self, tx):
        self.tx = tx

    def serialize(self):
        return self.tx.raw


class TestSQLDB(unittest.TestCase):

    def setUp(self):
        self.first_sync = False
        self.daemon_height = 1
        self.sql = SQLDB(self, ':memory:')
        self.timer = Timer('BlockProcessor')
        self.sql.open()
        self._current_height = 0
        self._txos = {}

    def _make_tx(self, output, txi=None):
        tx = get_tx().add_outputs([output])
        if txi is not None:
            tx.add_inputs([txi])
        self._txos[output.ref.hash] = output
        return OldWalletServerTransaction(tx), tx.hash

    def get_channel(self, title, amount, name='@foo'):
        claim = Claim()
        claim.channel.title = title
        channel = Output.pay_claim_name_pubkey_hash(amount, name, claim, b'abc')
        channel.generate_channel_private_key()
        return self._make_tx(channel)

    def get_stream(self, title, amount, name='foo'):
        claim = Claim()
        claim.stream.title = title
        return self._make_tx(Output.pay_claim_name_pubkey_hash(amount, name, claim, b'abc'))

    def get_stream_update(self, tx, amount):
        claim = Transaction(tx[0].serialize()).outputs[0]
        return self._make_tx(
            Output.pay_update_claim_pubkey_hash(
                amount, claim.claim_name, claim.claim_id, claim.claim, b'abc'
            ),
            Input.spend(claim)
        )

    def get_stream_abandon(self, tx):
        claim = Transaction(tx[0].serialize()).outputs[0]
        return self._make_tx(
            Output.pay_pubkey_hash(claim.amount, b'abc'),
            Input.spend(claim)
        )

    def get_support(self, tx, amount):
        claim = Transaction(tx[0].serialize()).outputs[0]
        return self._make_tx(
            Output.pay_support_pubkey_hash(
                amount, claim.claim_name, claim.claim_id, b'abc'
             )
        )

    def get_controlling(self):
        for claim in self.sql.execute("select claim.* from claimtrie natural join claim"):
            txo = self._txos[claim['txo_hash']]
            controlling = txo.claim.stream.title, claim['amount'], claim['effective_amount'], claim['activation_height']
            return controlling

    def get_active(self):
        controlling = self.get_controlling()
        active = []
        for claim in self.sql.execute(
                f"select * from claim where activation_height <= {self._current_height}"):
            txo = self._txos[claim['txo_hash']]
            if controlling and controlling[0] == txo.claim.stream.title:
                continue
            active.append((txo.claim.stream.title, claim['amount'], claim['effective_amount'], claim['activation_height']))
        return active

    def get_accepted(self):
        accepted = []
        for claim in self.sql.execute(
                f"select * from claim where activation_height > {self._current_height}"):
            txo = self._txos[claim['txo_hash']]
            accepted.append((txo.claim.stream.title, claim['amount'], claim['effective_amount'], claim['activation_height']))
        return accepted

    def advance(self, height, txs):
        self._current_height = height
        self.sql.advance_txs(height, txs, {'timestamp': 1}, self.daemon_height, self.timer)
        return [otx[0].tx.outputs[0] for otx in txs]

    def state(self, controlling=None, active=None, accepted=None):
        self.assertEqual(controlling or [], self.get_controlling())
        self.assertEqual(active or [], self.get_active())
        self.assertEqual(accepted or [], self.get_accepted())

    def test_example_from_spec(self):
        # https://spec.lbry.com/#claim-activation-example
        advance, state = self.advance, self.state
        stream = self.get_stream('Claim A', 10*COIN)
        advance(13, [stream])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[],
            accepted=[]
        )
        advance(1001, [self.get_stream('Claim B', 20*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[],
            accepted=[('Claim B', 20*COIN, 0, 1031)]
        )
        advance(1010, [self.get_support(stream, 14*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 24*COIN, 13),
            active=[],
            accepted=[('Claim B', 20*COIN, 0, 1031)]
        )
        advance(1020, [self.get_stream('Claim C', 50*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 24*COIN, 13),
            active=[],
            accepted=[
                ('Claim B', 20*COIN, 0, 1031),
                ('Claim C', 50*COIN, 0, 1051)]
        )
        advance(1031, [])
        state(
            controlling=('Claim A', 10*COIN, 24*COIN, 13),
            active=[('Claim B', 20*COIN, 20*COIN, 1031)],
            accepted=[('Claim C', 50*COIN, 0, 1051)]
        )
        advance(1040, [self.get_stream('Claim D', 300*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 24*COIN, 13),
            active=[('Claim B', 20*COIN, 20*COIN, 1031)],
            accepted=[
                ('Claim C', 50*COIN, 0, 1051),
                ('Claim D', 300*COIN, 0, 1072)]
        )
        advance(1051, [])
        state(
            controlling=('Claim D', 300*COIN, 300*COIN, 1051),
            active=[
                ('Claim A', 10*COIN, 24*COIN, 13),
                ('Claim B', 20*COIN, 20*COIN, 1031),
                ('Claim C', 50*COIN, 50*COIN, 1051)],
            accepted=[]
        )
        # beyond example
        advance(1052, [self.get_stream_update(stream, 290*COIN)])
        state(
            controlling=('Claim A', 290*COIN, 304*COIN, 13),
            active=[
                ('Claim B', 20*COIN, 20*COIN, 1031),
                ('Claim C', 50*COIN, 50*COIN, 1051),
                ('Claim D', 300*COIN, 300*COIN, 1051),
            ],
            accepted=[]
        )

    def test_competing_claims_subsequent_blocks_height_wins(self):
        advance, state = self.advance, self.state
        advance(13, [self.get_stream('Claim A', 10*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[],
            accepted=[]
        )
        advance(14, [self.get_stream('Claim B', 10*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[('Claim B', 10*COIN, 10*COIN, 14)],
            accepted=[]
        )
        advance(15, [self.get_stream('Claim C', 10*COIN)])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[
                ('Claim B', 10*COIN, 10*COIN, 14),
                ('Claim C', 10*COIN, 10*COIN, 15)],
            accepted=[]
        )

    def test_competing_claims_in_single_block_position_wins(self):
        advance, state = self.advance, self.state
        stream = self.get_stream('Claim A', 10*COIN)
        stream2 = self.get_stream('Claim B', 10*COIN)
        advance(13, [stream, stream2])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[('Claim B', 10*COIN, 10*COIN, 13)],
            accepted=[]
        )

    def test_competing_claims_in_single_block_effective_amount_wins(self):
        advance, state = self.advance, self.state
        stream = self.get_stream('Claim A', 10*COIN)
        stream2 = self.get_stream('Claim B', 11*COIN)
        advance(13, [stream, stream2])
        state(
            controlling=('Claim B', 11*COIN, 11*COIN, 13),
            active=[('Claim A', 10*COIN, 10*COIN, 13)],
            accepted=[]
        )

    def test_winning_claim_deleted(self):
        advance, state = self.advance, self.state
        stream = self.get_stream('Claim A', 10*COIN)
        stream2 = self.get_stream('Claim B', 11*COIN)
        advance(13, [stream, stream2])
        state(
            controlling=('Claim B', 11*COIN, 11*COIN, 13),
            active=[('Claim A', 10*COIN, 10*COIN, 13)],
            accepted=[]
        )
        advance(14, [self.get_stream_abandon(stream2)])
        state(
            controlling=('Claim A', 10*COIN, 10*COIN, 13),
            active=[],
            accepted=[]
        )

    def test_winning_claim_deleted_and_new_claim_becomes_winner(self):
        advance, state = self.advance, self.state
        stream = self.get_stream('Claim A', 10*COIN)
        stream2 = self.get_stream('Claim B', 11*COIN)
        advance(13, [stream, stream2])
        state(
            controlling=('Claim B', 11*COIN, 11*COIN, 13),
            active=[('Claim A', 10*COIN, 10*COIN, 13)],
            accepted=[]
        )
        advance(15, [self.get_stream_abandon(stream2), self.get_stream('Claim C', 12*COIN)])
        state(
            controlling=('Claim C', 12*COIN, 12*COIN, 15),
            active=[('Claim A', 10*COIN, 10*COIN, 13)],
            accepted=[]
        )

    def test_trending(self):
        advance, state = self.advance, self.state
        no_trend = self.get_stream('Claim A', COIN)
        downwards = self.get_stream('Claim B', COIN)
        up_small = self.get_stream('Claim C', COIN)
        up_medium = self.get_stream('Claim D', COIN)
        up_biggly = self.get_stream('Claim E', COIN)
        claims = advance(1, [up_biggly, up_medium, up_small, no_trend, downwards])
        for window in range(1, 8):
            advance(TRENDING_WINDOW * window, [
                self.get_support(downwards, (20-window)*COIN),
                self.get_support(up_small, int(20+(window/10)*COIN)),
                self.get_support(up_medium, (20+(window*(2 if window == 7 else 1)))*COIN),
                self.get_support(up_biggly, (20+(window*(3 if window == 7 else 1)))*COIN),
            ])
        results = self.sql._search(order_by=['trending_local'])
        self.assertEqual([c.claim_id for c in claims], [claim_id(c['claim_hash']) for c in results])
        self.assertEqual([10, 6, 2, 0, -2], [int(c['trending_local']) for c in results])
        self.assertEqual([53, 38, -32, 0, -6], [int(c['trending_global']) for c in results])
        self.assertEqual([4, 4, 2, 0, 1], [int(c['trending_group']) for c in results])
        self.assertEqual([53, 38, 2, 0, -6], [int(c['trending_mixed']) for c in results])
