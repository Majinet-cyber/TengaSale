# Tenga Device Location & Theft Recovery Policy

Version 2026.1 · Owner: Tenga Protect Compliance · Review before production location enablement

## 1. Purpose and scope

This policy governs TengaSale, Emajinet, employees, contractors, merchants, providers and agents involved in lost-device and theft recovery. It applies to customer consent, protection locks, location data, documents, certificates, audits and provider processing.

## 2. Definitions and privacy principles

A Protect case is a customer-initiated lost or stolen device record. A payment lock controls contractual device functionality. A theft lock protects a device reported stolen. They are separate controls. Tenga minimises collection, restricts access, records sensitive actions and makes customer recovery transparent.

## 3. Prohibited use

No employee, contractor, merchant, officer or agent of TengaSale may request, obtain, disclose or attempt to obtain the location of a customer's device for the purpose of collecting a debt or determining the customer's physical location in connection with arrears.

Location must not be used for missed payments, delinquency, collections, repayment scoring, repossession, merchant or underwriter requests, suspected avoidance, or ordinary account management. A violation is serious misconduct and must be investigated as a security incident.

## 4. Permitted recovery use and customer consent

Location may be requested only for a verified lost/stolen case after identity, ownership and device/IMEI verification; explicit versioned consent with strong confirmation; required police evidence for theft; compliance review; and maker-checker approval. Consent records include time, policy version, person, method, IP and available device metadata. Consent can be revoked and ends when the case closes.

## 5. Police reports and legal authority

Theft reports require police documentation. Tenga distinguishes customer-reported, police-report-provided and police-report-verified states. Legal or court authority is collected where policy or law requires it. Uploads are restricted, integrity-hashed and available only to authorised staff.

## 6. Authorised personnel and maker-checker

Protect Officer, Protect Supervisor, Compliance Officer and Protect Administrator are explicit least-privilege assignments. Administrator, HQ or superuser status does not grant recovery location access. An officer requests access; a different supervisor or compliance officer approves it. Support, merchant, underwriting, finance and collections roles cannot view coordinates.

## 7. Location limits and retention

Sessions are temporary, expire automatically and never silently extend. Each deliberate current-location request is audited. Continuous polling is prohibited. Last-known data must be labelled as historical. Precise coordinates are excluded from ordinary logs, dashboards, analytics and messaging. Compliance configures minimum-necessary retention; no Malawi legal retention period is assumed without counsel.

## 8. Device locking and recovery

Payment, theft, lost-device, fraud and administrative security locks retain distinct reasons. Protection messages disclose no owner address, identity or account balance. Recovery requires identity, case and IMEI confirmation. Closing a recovered case terminates every session and consent before an authorised theft unlock.

## 9. Customer transparency

Customers can see the policy, consent and authorised access history. Public certificate and IMEI checks expose only device and registry status, never customer name, contact details, identity, address or balance.

## 10. Audit, confidentiality and incident response

Protect audit records are immutable through ordinary administration. Permission denials, document changes, location requests, role changes and unusual access patterns are reviewed by Compliance. Personnel keep recovery information confidential. Suspected misuse triggers access suspension, evidence preservation, security response and disciplinary review.

## 11. Law enforcement, processors and exceptions

Information is shared with law enforcement or third-party providers only under lawful authority, appropriate permission, minimum-necessary disclosure and recorded purpose. Providers must meet contractual privacy and security controls. Exceptions require documented Compliance and legal approval and may never authorise debt-collection location use.

## 12. Governance and review

Compliance owns this policy, approves provider activation and retention schedules, reviews high-risk access, and records revisions. Security and Product review the control design at least annually and before enabling a new location provider, jurisdiction, insurer or Protect+ tier.
