## Feature Idea Log

### Item Slot Fingerprinting (Family 2 — Discontinuity Detection)

Hypothesis: inventory slot placement for frequently-purchased items (e.g.
always slotting Blink Dagger in position 1) is a motor habit specific to
an individual player, distinct from item choice itself (which reflects
game knowledge and is shared across players of similar skill). A sudden,
sustained shift in slot placement habits — independent of any change in
skill level — is a candidate signal for account handoff (Class 2, 4) or
active boosting (Class 3), since a new person playing the account brings
their own habitual slot placement, not the original owner's.

Planned features:
- item_slot_consistency_score
- item_slot_changepoint_detected
- item_slot_changepoint_permanence
- signature_item_slot_mode

Requires full match history parsing (not just earliest-50), since the
changepoint can occur anywhere in an account's history. Deferred to the
Family 2 discontinuity feature build.