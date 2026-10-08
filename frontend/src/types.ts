export interface Step { key: string; label: string; status: "pending" | "running" | "done" | "error" | "skipped"; detail: string }
export interface Analysis {
  image_id: number; is_product_image: boolean; product_visibility_score: number; quality_score: number;
  composition_score: number; feature_visibility_score: number; ecommerce_score: number;
  image_type: string; visible_features: string[]; recommended: boolean;
}
export interface JobImage {
  id: number; state: string; rejected_reason: string | null; selected: boolean; is_featured: boolean;
  position: number | null; ai_score: number | null; analysis: Analysis | null;
  title: string | null; alt_text: string | null; caption: string | null; description: string | null;
  wordpress_media_id: number | null; width: number | null; height: number | null;
  source_preview: string; processed_preview: string | null;
}
export interface Check { name: string; ok: boolean; detail: string }
export interface Job {
  id: string; product_name: string; product_id: number | null; source_product_url: string | null;
  original_source_url: string | null; destination_site: string | null; mode: "update" | "create";
  status: string; error_code: string | null; error_message: string | null;
  progress: Step[]; warnings: string[]; verification: Check[]; result_url: string | null; images: JobImage[];
}
export interface Match { id: number | null; name: string; permalink: string; status: string; score: number }
export interface SearchResult { matches: Match[]; auto_selected_id: number | null; auto_selected_url: string | null; threshold: number }
export interface SourceCandidate { url: string; score: number; kind: string; reasons: string[] }
export interface Inspection {
  product: { id: number | null; name: string; url: string; status: string };
  description_preview: string; candidates: SourceCandidate[]; selected_url: string | null;
  ambiguous: boolean; destination_host: string; message: string | null;
}
export interface Settings { auto_publish: boolean; headless: boolean; match_threshold: number; strip_source_from_description: boolean }
export type BatchStatus = "queued" | "running" | "published" | "needs_review" | "failed" | "skipped" | "cancelled";
export interface BatchItem {
  product_id: number | null; name: string; permalink: string; status: BatchStatus; message: string;
  job_id: string | null; result_url: string | null; step: string | null; steps_done: number; steps_total: number;
}
export interface Batch {
  id: string; state: "running" | "done" | "cancelled"; total: number; counts: Record<string, number>; items: BatchItem[];
}
export const BUSY = ["queued", "running", "processing", "publishing"];