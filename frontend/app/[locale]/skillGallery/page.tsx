"use client";

// Direct-URL entry for the KnowEvo skill gallery. Does NOT replace
// /skillTemplate (table panel stays). Navigation item is seeded for
// SU/ADMIN via v2.5.5_kw_013_l10_nav_rbac.sql.
import SkillGalleryPage from "@/features/skillGallery/SkillGalleryPage";

export default function Page() {
  return <SkillGalleryPage />;
}
