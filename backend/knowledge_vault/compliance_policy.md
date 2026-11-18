---
id: compliance_policy_v4
domain: policy
risk_tier: TIER_1_READ
version: "4.2"
---

# Enterprise Compliance Policy

## Data Classification

| Level | Label | Description | Handling |
|---|---|---|---|
| L1 | PUBLIC | Marketing materials, public docs | No restrictions |
| L2 | INTERNAL | Internal process docs | Employee access only |
| L3 | CONFIDENTIAL | Customer data, contracts | Encrypted at rest and in transit |
| L4 | RESTRICTED | PII, financial records, health data | HITL required for any mutation |

## PII Handling Rules
- PII must never be stored in raw form in cache or logs
- All PII fields must use SHA-256 pseudonymization in analytics pipelines
- Email addresses, phone numbers, SSN, and obfuscated variants (name[at]domain) are classified as L4
- Any system that processes L4 data requires TIER_3_MUTATE access and audit logging

## Access Control Tiers

### TIER_1_READ
- Permitted: Query, read, search, retrieve
- Requires: Valid tenant_id + user_id
- Audit: Logged to audit_log table

### TIER_2_COMPUTE
- Permitted: Batch analysis, report generation, export
- Requires: TIER_1_READ + role=analyst or above
- Audit: Logged with computation hash

### TIER_3_MUTATE
- Permitted: Write, delete, workflow transitions, recalls, approvals
- Requires: Explicit HITL approval from authorized reviewer
- Audit: SHA-256 hash-chained audit log entry

## Incident Response SLAs
- P1 (data breach, system down): 1-hour response, 4-hour resolution
- P2 (degraded service, compliance gap): 4-hour response, 24-hour resolution
- P3 (non-critical): 24-hour response, 72-hour resolution

## Retention Schedules
- Audit logs: 7 years (regulatory requirement)
- Session facts: 90 days
- Redis cache: 300 seconds (5 minutes)
- HITL records: 5 years

[[safety_protocols]] [[product_catalog]]
