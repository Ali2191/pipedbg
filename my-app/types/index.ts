export interface ParsedRFQ {
  customer_name: string | null;
  customer_company: string | null;
  customer_email: string | null;
  project_name: string | null;
  material_type: string | null;
  material_form: string | null;
  thickness_inches: number | null;
  dimensions: string | null;
  quantity: number | null;
  finish_type: string | null;
  delivery_date: string | null;
  special_requirements: string | null;
  confidence_score: number;
  missing_info: string[];
}
