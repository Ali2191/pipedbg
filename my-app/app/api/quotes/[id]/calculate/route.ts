import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { calculateQuote } from "@/lib/price-calculator";

export async function POST(req: Request, { params }: { params: { id: string } }) {
  const body = await req.json();
  const quote = await prisma.quote.findUnique({ where: { id: params.id }, include: { shop: { include: { settings: true } } } });
  if (!quote || !quote.shop.settings) return NextResponse.json({ error: "Quote/settings not found" }, { status: 404 });

  const material = await prisma.material.findUnique({ where: { id: body.materialId } });
  if (!material) return NextResponse.json({ error: "Material not found" }, { status: 404 });

  const result = calculateQuote({
    materialId: material.id,
    quantity: Number(body.quantity ?? quote.quantity),
    dimensions: String(body.dimensions ?? quote.dimensions ?? ""),
    thickness: Number(body.thickness ?? quote.thickness ?? 0.125),
    laborHours: Number(body.laborHours ?? quote.laborHours),
    finishType: String(body.finish ?? quote.finish ?? "raw"),
    marginPercent: Number(body.marginPercent ?? quote.marginPercent),
    shopSettings: quote.shop.settings,
    material,
  });

  await prisma.quote.update({ where: { id: params.id }, data: { ...result, materialId: material.id, laborHours: Number(body.laborHours ?? quote.laborHours) } });
  return NextResponse.json(result);
}
