---
date: 2026-04-05
title: "[Breaking] Schema change: Property_Maintenance_Investigations"
commit: 2072cd9c
tables:
  - Property_Maintenance_Investigations
change_types:
  - column-added
  - column-renamed
  - description-removed
breaking: true
---

**2026-04-05 10:18 AM EDT** | [`2072cd9c`](https://github.com/junedata/arcgis-schema-tracker/commit/2072cd9ce4c215d3d2e60ed7b50025339689303a)

> **This commit contains a potential breaking change.** A breaking change occurs when a field is removed, renamed, or has its type changed -- any downstream query, pipeline, or application that references the old field name or type will fail silently or return no data.

## `Property_Maintenance_Investigations`

**Fields renamed (6) -- breaking change:**
- `Investigation_Desc` -> `INVESTIGATION_DESC`
- `Investigation_Type` -> `INVESTIGATION_TYPE`
- `Project` -> `PROJECT`
- `B1_APPL_STATUS` -> `RECORD_STATUS`
- `LAST_INSEPCTION_DATE` -> `LAST_INSPECTION_DATE` _(fixed typo)_
- `PERMIT_ID` -> `RECORD_ID`

**Fields added (1):**
- `RECORD_STATUS_DATE`

**Table configuration:**
- **Table description:** missing
- **Field descriptions:** 72% complete
- **Column naming:** UPPER_SNAKE: 11, mixed: 6, PascalCase: 1

[View full diff ->](https://github.com/junedata/arcgis-schema-tracker/commit/2072cd9ce4c215d3d2e60ed7b50025339689303a#schemas-Property_Maintenance_Investigations.FeatureServer.0.schema.json)
[REST API ->](https://services3.arcgis.com/dty2kHktVXHrqO8i/ArcGIS/rest/services/Property_Maintenance_Investigations/FeatureServer/0/query?where=1%3D1&outFields=*&orderByFields=OBJECTID+DESC&f=html)

---

## Why it matters

RECORD_STATUS_DATE is a new field that appears to be updated when the RECORD_STATUS field changes. Three field names are changed to match the UPPER_SNAKE naming convention, two are changed to more accurately reflect their purpose, and one is renamed to correct a typo.

## Note for developers

**Action required:** Any pipeline or script referencing the removed/renamed fields by name will fail on its next run. Review the field list above and update references before the next execution.

Downstream services that use different field names will likely fail and produce errors. Review the field list above and update references before the your next code execution.

## Open questions/comments

- Maintainers should add a service-level description to the schema for clarify of purpose.
- Maintainers should add descriptions where missing for each field.
- Specific field names, like 'ObjectID', may be difficult to change without rebuilding indexes or potentially breaking existing queries. Other field names, like 'DW_*', are likely created by JOINs on other tables using mixed case.
