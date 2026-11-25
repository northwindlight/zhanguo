"""BC prefix equivalence and leakage checks using native observations."""

import json

import torch

from bc_train import ActionBC, action_payload, loss_and_hits
from memory_map import Op
from model import Event, Policy
from verify import close, collated, example_episode


def main():
    torch.set_num_threads(2)
    torch.manual_seed(20261125)
    rows = example_episode()["teacher"][:8]
    batches = [collated(row[0]) for row in rows]
    labels = [torch.tensor([row[1]]) for row in rows]
    model = Policy(width=96, heads=6, inner=192, board_width=64).eval()
    bc = ActionBC(model, checkpoint_board=False).eval()
    result = {}
    with torch.no_grad():
        for sparse in (False, True):
            name = "sparse" if sparse else "dense"
            actual, _ = bc(batches, labels, sparse=sparse)
            history = None
            worst = 0.0
            for batch, label, expected in zip(batches, labels, actual):
                snapshot = model.snapshot(batch, 0)
                _, history, _ = model._encode_events(
                    snapshot.global_token + model.event_kind.weight[Event.OBSERVATION],
                    history, sparse=sparse)
                state, _, _ = model.decode(
                    torch.zeros((1, 1, 4, 4), dtype=torch.long),
                    torch.tensor([[Op.GAME]]), torch.zeros((1, 1), dtype=torch.long),
                    snapshot, history, sparse=sparse)
                replay = model.action_logits(state[:, 0], batch, snapshot)
                worst = max(worst, close(replay, expected))
                _, history, _ = model.events(
                    torch.tensor([[Event.ACTION]]), action_payload(batch, label)[None],
                    history, sparse=sparse)
            altered = list(labels)
            for i in range(4, len(altered)):
                altered[i] = (altered[i] + 1) % batches[i]["cand"].shape[1]
            modified, _ = bc(batches, altered, sparse=sparse)
            future_error = max(close(actual[i], modified[i]) for i in range(5))
            result[name] = dict(prefix_replay_error=worst, future_label_error=future_error)
    bc.train()
    scores, kl = bc(batches, labels, sparse=True)
    ce, _, n = loss_and_hits(scores, labels)
    (ce / n + 0.01 * kl).backward()
    assert model.thought.weight.grad is None and model.value[0].weight.grad is None
    for module in (model.delta[0].qkv, model.sparse.index_query, model.action_query):
        grad = module.weight.grad
        assert grad is not None and torch.isfinite(grad).all() and grad.norm() > 0
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
