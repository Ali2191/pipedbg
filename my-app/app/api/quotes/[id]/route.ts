import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET(_: NextRequest, { params }: { params: { id: string } }) {
  const quote = await prisma.quote.findUnique({ where: { id: params.id }, include: { material: true } });
  if (!quote) return NextResponse.json({ error: "Not found" }, { status: 404 });
  return NextResponse.json(quote);
}

export async function PATCH(req: NextRequest, { params }: { params: { id: string } }) {
  const data = await req.json();
  const quote = await prisma.quote.update({ where: { id: params.id }, data });
  return NextResponse.json(quote);
}
