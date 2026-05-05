import { openai } from "@/lib/openai";
import { ParsedRFQ } from "@/types";

const EXAMPLE_RFQS = [
  "Need 500 brackets, 4x2, 304 SS, brushed finish. Need by June 12.",
  "Quote 80 aluminum 6061 plates 12x12x0.25, powder coat black, certs required.",
  "Please quote tube assemblies, quantity 150, material TBD, delivery in two weeks.",
].join("\n");

export async function parseRFQEmail(emailBody: string, attachmentText = ""): Promise<ParsedRFQ> {
  const prompt = `You are an expert manufacturing estimator. Parse RFQ and return JSON.

Rules:
- Parse only requested fields.
- If unclear, set null and explain in missing_info.
- Convert gauge to inches if possible.
- delivery_date must be YYYY-MM-DD or null.
- confidence_score in [0,1].

Examples:
${EXAMPLE_RFQS}

Email:
"""${emailBody}"""

Attachment text:
"""${attachmentText}"""`;

  const response = await openai.chat.completions.create({
    model: "gpt-4o",
    response_format: { type: "json_object" },
    messages: [
      { role: "system", content: "Return ONLY valid JSON." },
      { role: "user", content: prompt },
    ],
    temperature: 0.1,
  });

  const content = response.choices[0]?.message?.content ?? "{}";
  const data = JSON.parse(content);
  return {
    customer_name: data.customer_name ?? null,
    customer_company: data.customer_company ?? null,
    customer_email: data.customer_email ?? null,
    project_name: data.project_name ?? null,
    material_type: data.material_type ?? null,
    material_form: data.material_form ?? null,
    thickness_inches: data.thickness_inches ?? null,
    dimensions: data.dimensions ?? null,
    quantity: data.quantity ?? null,
    finish_type: data.finish_type ?? null,
    delivery_date: data.delivery_date ?? null,
    special_requirements: data.special_requirements ?? null,
    confidence_score: Number(data.confidence_score ?? 0),
    missing_info: Array.isArray(data.missing_info) ? data.missing_info : [],
  };
}
