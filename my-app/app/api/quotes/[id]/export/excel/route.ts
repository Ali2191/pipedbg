import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { buildQuoteExcel } from "@/lib/excel-generator";

export async function POST(_: Request, { params }: { params: { id: string } }) {
  const quote = await prisma.quote.findUnique({ where: { id: params.id }, include: { shop: true } });
  if (!quote) return NextResponse.json({ error: "Quote not found" }, { status: 404 });
  const file = buildQuoteExcel(quote, quote.shop);
  return new Response(file as any, { headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Content-Disposition": `attachment; filename=quote-${quote.id}.xlsx` } });
}
