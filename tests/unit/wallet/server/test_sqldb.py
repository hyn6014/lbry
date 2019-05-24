import unittest
import ecdsa
import hashlib
from binascii import hexlify, unhexlify
from torba.client.constants import COIN, NULL_HASH32

from lbrynet.schema.claim import Claim
from lbrynet.wallet.server.db import SQLDB
from lbrynet.wallet.server.trending import TRENDING_WINDOW
from lbrynet.wallet.server.canonical import FindShortestID
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
        private_key = ecdsa.SigningKey.from_string(b'c'*32, curve=ecdsa.SECP256k1, hashfunc=hashlib.sha256)
        channel.private_key = private_key.to_pem().decode()
        channel.claim.channel.public_key_bytes = private_key.get_verifying_key().to_der()
        channel.script.generate()
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
        self.assertEqual([c.claim_id for c in claims], [hexlify(c['claim_hash'][::-1]).decode() for c in results])
        self.assertEqual([10, 6, 2, 0, -2], [int(c['trending_local']) for c in results])
        self.assertEqual([53, 38, -32, 0, -6], [int(c['trending_global']) for c in results])
        self.assertEqual([4, 4, 2, 0, 1], [int(c['trending_group']) for c in results])
        self.assertEqual([53, 38, 2, 0, -6], [int(c['trending_mixed']) for c in results])

    @staticmethod
    def _get_x_with_claim_id_prefix(getter, prefix, cached_iteration=None):
        iterations = 100
        for i in range(cached_iteration or 1, iterations):
            stream = getter(f'claim #{i}', COIN)
            if stream[0].tx.outputs[0].claim_id.startswith(prefix):
                print(f'Found "{prefix}" in {i} iterations.')
                return stream
        raise ValueError(f'Failed to find "{prefix}" in {iterations} iterations.')

    def get_channel_with_claim_id_prefix(self, prefix, cached_iteration):
        return self._get_x_with_claim_id_prefix(self.get_channel, prefix, cached_iteration)

    def get_stream_with_claim_id_prefix(self, prefix, cached_iteration):
        return self._get_x_with_claim_id_prefix(self.get_stream, prefix, cached_iteration)

    def test_canonical_name(self):
        advance = self.advance
        tx_abc = self.get_stream_with_claim_id_prefix('abc', 65)
        tx_ab = self.get_stream_with_claim_id_prefix('ab', 42)
        tx_a = self.get_stream_with_claim_id_prefix('a', 2)
        advance(1, [tx_a])
        advance(2, [tx_ab])
        advance(3, [tx_abc])
        r_a, r_ab, r_abc = self.sql._search(order_by=['^height'])
        self.assertEqual("foo", r_a['canonical'])
        self.assertEqual(f"foo#ab", r_ab['canonical'])
        self.assertEqual(f"foo#abc", r_abc['canonical'])

        tx_ab = self.get_channel_with_claim_id_prefix('ab', 72)
        tx_a = self.get_channel_with_claim_id_prefix('a', 1)
        advance(4, [tx_a])
        advance(5, [tx_ab])

        tx_c = self.get_stream_with_claim_id_prefix('c', 2)
        tx_cd = self.get_stream_with_claim_id_prefix('cd', 2)
        advance(6, [tx_c])
        advance(7, [tx_cd])

        r_a, r_ab, r_abc = self.sql._search(order_by=['^height'])
        self.assertEqual("foo", r_a['canonical'])
        self.assertEqual(f"foo#ab", r_ab['canonical'])
        self.assertEqual(f"foo#abc", r_abc['canonical'])

    def test_canonical_find_shortest_id(self):
        new_hash = unhexlify('abcdef0123456789beef')[::-1]
        other0 = unhexlify('1bcdef0123456789beef')[::-1]
        other1 = unhexlify('ab1def0123456789beef')[::-1]
        other2 = unhexlify('abc1ef0123456789beef')[::-1]
        other3 = unhexlify('abcdef0123456789bee1')[::-1]
        f = FindShortestID()
        self.assertEqual('', f.finalize())
        f.step(other0, new_hash)
        self.assertEqual('#a', f.finalize())
        f.step(other1, new_hash)
        self.assertEqual('#abc', f.finalize())
        f.step(other2, new_hash)
        self.assertEqual('#abcd', f.finalize())
        f.step(other3, new_hash)
        self.assertEqual('#abcdef0123456789beef', f.finalize())
