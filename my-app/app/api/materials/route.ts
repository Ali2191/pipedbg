import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET(req: NextRequest) {
  const shopId = req.nextUrl.searchParams.get("shopId");
  if (!shopId) return NextResponse.json({ error: "shopId required" }, { status: 400 });
  const materials = await prisma.material.findMany({ where: { shopId }, orderBy: { updatedAt: "desc" } });
  return NextResponse.json(materials);
}

export async function POST(req: Request) {
  const data = await req.json();
  const created = await prisma.material.create({ data });
  return NextResponse.json(created, { status: 201 });
}

export async function PATCH(req: Request) {
  const data = await req.json();
  const updated = await prisma.material.update({ where: { id: data.id }, data });
  return NextResponse.json(updated);
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
  await prisma.material.delete({ where: { id } });
  return NextResponse.json({ ok: true });
}
