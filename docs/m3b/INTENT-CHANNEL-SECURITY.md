# Intent channel: mandatory authenticated sender boundary (design; ASTA-DECISION-007 workstream C)

Status: **offline design and fail-closed implementation only.** The message schema is unchanged from `orders/channel.py` (version 1). No exchange credential, key, signing code or network transport exists; the channel is imported by nothing outside `asta.orders`.

## 1. Boundary
```
 asta-app VM (no credentials)              asta-cred VM (vault + connector, the only credential holder)
   Dispatcher decision ──► seal(envelope) ═══ intent channel ═══► Receiver.open(wire, now)
        (needs Authenticator.tag_for)        (one TCP port,        1 size cap  2 parse  3 AUTHENTICATE
                                              outbound-only from   4 sender allowlist  5 window/expiry
                                              the app VM)          6 intent validation  7 durable replay accept
                                                                   8 -> gateway boundary -> Dispatcher
```
A message reaches the dispatcher only after all eight steps. Step 3 runs before any envelope field is trusted; step 7 is a single database transaction.

## 2. Mandatory properties and where they are enforced
| Property | Enforcement | Test |
|---|---|---|
| The authenticator is mandatory and, in this build, absent | `Receiver` has no default authenticator; the only one shipped is `FailClosedAuthenticator` (verify = False, tag_for raises) | `test_without_a_valid_authenticator_nothing_is_ever_accepted` |
| Replay/expiry state is durable | `Receiver` refuses a non-durable `ReplayStore` (ValueError); `SqlReplayStore.accept` = one transaction on `channel_senders` (row lock) + `channel_nonces` | `test_a_non_durable_replay_store_is_refused...`, restart test |
| A restart cannot re-accept a message | high-water sequence per sender is in PostgreSQL; a trigger refuses any decrease or delete | `test_a_replay_is_refused_across_a_process_restart` |
| Concurrent receivers cannot both accept | `SELECT ... FOR UPDATE` on the sender row | `test_concurrent_receivers_accept_one_message_exactly_once` (8 threads) |
| Forged, expired, oversized, wrong-sender, malformed messages are refused and consume nothing | reason codes `NOT_AUTHENTIC`, `EXPIRED`, `TOO_LARGE`, `SENDER_NOT_ALLOWED`, `MALFORMED`; replay state changes only on accept | `test_forged_expired_oversized_wrong_sender...` |
| A replayed intent is harmless even if the channel layer failed | the decision-id claim (`order_intents` PRIMARY KEY) makes a second delivery a no-op | `test_order_path.py` duplicate tests |
| The credential side re-validates everything | `Intent.validate`, the gateway boundary, the pre-trade guard | gateway tests |

## 3. Residual items (not closed; the schema is frozen by the directive)
* Key id is read from unauthenticated JSON before verification (bounded to 2 KiB, errors caught). Closure when the schema may change: a fixed-offset header inside the authenticated frame.
* No receiver/audience field: a key valid for two receivers would be accepted by both. Closure: audience inside the authenticated payload, or one key per receiver (recommended, no schema change).
* Sequence consumption happens before the dispatcher acts: a crash between accept and dispatch loses the message (fail-safe); the sender retries with a fresh sequence/nonce and the same decision id.
* Nonce rows are pruned one hour after expiry; refusal as `EXPIRED` happens long before, and the sequence high-water mark is never pruned.
* The authenticator (algorithm, key ceremony, rotation, expiry of keys), channel transport (socket permissions or mutual TLS) and sender identity provisioning are reviewed-design items for M3-B proper (F-05, F-06).
