---
id: product_catalog_v2
domain: product
risk_tier: TIER_1_READ
version: "2.0"
---

# Product Catalog — Enterprise SKU Reference

## Active Products

### SKU-001: Enterprise Data Platform License
- Description: Annual license for core data platform
- Price tier: Enterprise
- Minimum order: 10 seats
- Renewal: Auto-renewal 60 days before expiry unless cancelled
- Support SLA: 24/7 P1 response < 1 hour

### SKU-002: AI Assistant Module
- Description: Agentic AI module with RAG capabilities
- Requires: SKU-001 as base license
- Price tier: Add-on
- Deployment: Cloud or on-premises
- Compliance: SOC 2 Type II, ISO 27001

### SKU-003: Compliance Analytics Pack
- Description: Automated compliance reporting and audit trail
- Domains: GDPR, HIPAA, SOC 2, ISO 27001
- Output formats: PDF, JSON, CSV
- Retention: 7-year audit log

### SKU-099: Legacy Integration Adapter (deprecated)
- Status: END-OF-LIFE — final support date 2025-12-31
- Replacement: SKU-002 with migration path available
- Note: No new activations. Existing customers must migrate.

## Ordering Process
1. Submit purchase order via procurement portal (TIER_2_COMPUTE)
2. Orders > $50,000 require TIER_3_MUTATE approval (HITL gate)
3. License keys provisioned within 2 business days

[[safety_protocols]] [[compliance_policy]]
