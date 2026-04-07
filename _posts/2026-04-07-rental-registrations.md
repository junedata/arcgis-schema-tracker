---
date: 2026-04-07
title: "[Info] Schema update: Rental_Registrations"
commit: 8367e26b
tables:
  - Rental_Registrations
change_types:
  - field-modified
breaking: false
---

**2026-04-07 03:33 PM EDT** | [`8367e26b`](https://github.com/junedata/arcgis-schema-tracker/commit/8367e26be6b46bb6f2290337bee8179e061aed30)



## `Rental_Registrations`

**Fields modified (2):**
- `Record_ID`
  - description changed
- `b1_alt_ID`
  - description changed

- **Table description:** missing
- **Field descriptions:** 97% complete
- **Column naming:** mixed: 27, PascalCase: 3, UPPER_SNAKE: 1

[View full diff ->](https://github.com/junedata/arcgis-schema-tracker/commit/8367e26be6b46bb6f2290337bee8179e061aed30#schemas-Rental_Registrations.FeatureServer.0.schema.json)
[REST API ->](https://services3.arcgis.com/dty2kHktVXHrqO8i/ArcGIS/rest/services/Rental_Registrations/FeatureServer/0/query?where=1%3D1&outFields=*&orderByFields=OBJECTID+DESC&f=html)

---

## Why it matters -- developers

It appears that a UNIQUE constraint was added to the Record_ID field, and removed from the b1_alt_ID field. The change probably won't affect any downstream applications.