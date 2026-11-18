---
id: safety_protocols_v3
domain: policy
risk_tier: TIER_1_READ
version: "3.1"
---

# Safety Protocols — Batch Operations

## Batch Verification Procedure

All batches must be verified before release. Batch identifiers follow the format LOT-XXXXX (5-digit zero-padded). SKU identifiers follow SKU-XXX (3-digit zero-padded).

### Standard Verification Steps
1. Confirm batch LOT number matches production record
2. Verify expiry date is at least 90 days from release date
3. Check temperature log — cold chain must remain between 2°C and 8°C
4. Inspect seal integrity on 10% random sample
5. Run QC assay — results must be within ±5% of specification
6. Record triage status as PASS, FAIL, or HOLD in the system

### Triage Status Definitions
- **PASS**: All 5 verification steps passed. Batch cleared for distribution.
- **FAIL**: One or more critical steps failed. Batch quarantined immediately.
- **HOLD**: Non-critical deviation found. Senior QC review required within 24 hours.

### Critical Failure Triggers (auto-FAIL)
- Temperature excursion > 2 hours outside 2–8°C range
- Broken seal count > 0.5% of sample
- QC assay deviation > 15% from specification

## Emergency Recall Protocol

If a batch is recalled post-distribution:
1. Issue recall code RCL-YYYYMMDD-NNNN
2. Notify all distribution partners within 2 hours via ERP system
3. Create HITL record for TIER_3_MUTATE workflow_execute action
4. Await senior reviewer approval before triggering downstream notifications

[[compliance_policy]] [[product_catalog]]
