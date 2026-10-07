"""Jupiter receipt recovery plus complete finalized local-wallet inventory."""
from execution_state import StateError, raw
from execution_evidence import read_budget
from execution_jupiter_evidence import JupiterChainEvidence
from execution_jupiter import recover_observation
from mykonos_meme_scout.jupiter_read import JupiterSolanaEvidence, public_wallet


class JupiterReconciliationReader:
    def __init__(self,cfg):
        self.cfg=cfg

    def snapshot(self,store,*,deadline=None):
        rpc=JupiterSolanaEvidence(self.cfg)
        started=store.clock()
        with store.transaction(deadline=deadline) as c:
            floor=max((raw(r['proof']['slot']) for r in store.all(c,'receipts')),default=0)
        with read_budget(rpc,self.cfg['reconciliation_max_age_seconds'],deadline=deadline):
            positions,precision,balance,slot=rpc.wallet_snapshot(public_wallet(),minimum_slot=floor)
            snapshot={'wallet':public_wallet(),'observed_at':started,'chain_positions':positions,
                      'balance_lamports':str(balance),'chain_slot':slot,'commitment':'finalized',
                      'source':'LOCAL_PUBLIC_WALLET_AND_FINALIZED_RPC'}
            observation=recover_observation(store,JupiterChainEvidence(self.cfg,rpc=rpc,clock=store.clock),snapshot)
            if (any(raw(fill['proof']['slot'])>slot for fill in observation['recovered_fills'])
                    or any(raw(proof['slot'])>slot for proof in observation['failed_receipts'])):
                raise StateError('jupiter_wallet_snapshot_precedes_receipt')
            return observation
