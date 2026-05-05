import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { parseRFQEmail } from "@/lib/email-parser";

export async function POST(_: Request, { params }: { params: { id: string } }) {
  const quote = await prisma.quote.findUnique({ where: { id: params.id } });
  if (!quote) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const parsed = await parseRFQEmail(quote.rawEmailBody ?? "", "");

  const updated = await prisma.quote.update({
    where: { id: params.id },
    data: {
      customerName: parsed.customer_name,
      customerEmail: parsed.customer_email,
      customerCompany: parsed.customer_company,
      projectName: parsed.project_name,
      dimensions: parsed.dimensions,
      thickness: parsed.thickness_inches,
      quantity: parsed.quantity ?? 1,
      finish: parsed.finish_type,
      aiExtractedData: parsed as any,
      aiConfidence: parsed.confidence_score,
      extractionStatus: "EXTRACTED",
      status: "REVIEW",
    },
  });

  return NextResponse.json({ extracted: parsed, quote: updated });
}
