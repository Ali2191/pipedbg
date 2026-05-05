import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { buildQuotePdf } from "@/lib/pdf-generator";

export async function POST(_: Request, { params }: { params: { id: string } }) {
  const quote = await prisma.quote.findUnique({ where: { id: params.id }, include: { shop: { include: { settings: true } } } });
  if (!quote) return NextResponse.json({ error: "Quote not found" }, { status: 404 });
  const pdf = await buildQuotePdf(quote, quote.shop);
  return new Response(pdf, { headers: { "Content-Type": "application/pdf", "Content-Disposition": `attachment; filename=quote-${quote.id}.pdf` } });
}
