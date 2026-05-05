import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const DEFAULT_MATERIALS = [
  { name: "304 Stainless Steel", type: "sheet", unit: "lb", costPerUnit: 2.9 },
  { name: "316 Stainless Steel", type: "sheet", unit: "lb", costPerUnit: 3.2 },
  { name: "Aluminum 6061", type: "plate", unit: "lb", costPerUnit: 2.1 },
  { name: "Cold Rolled Steel", type: "sheet", unit: "lb", costPerUnit: 1.4 },
  { name: "Hot Rolled Steel", type: "plate", unit: "lb", costPerUnit: 1.2 },
];

export async function POST(req: Request) {
  const formData = await req.formData();
  const name = String(formData.get("name") ?? "").trim();
  const slug = String(formData.get("slug") ?? "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
  const ownerEmail = String(formData.get("ownerEmail") ?? "owner@demo-shop.com").toLowerCase();

  if (!name || !slug) return NextResponse.json({ error: "name and slug are required" }, { status: 400 });

  const owner = await prisma.user.findUnique({ where: { email: ownerEmail } });
  if (!owner) return NextResponse.json({ error: "Owner not found" }, { status: 404 });

  const shop = await prisma.shop.create({
    data: {
      name,
      slug,
      email: `quote@${slug}.quotefast.io`,
      ownerId: owner.id,
      settings: { create: {} },
      materials: { create: DEFAULT_MATERIALS },
    },
  });

  return NextResponse.json(shop, { status: 201 });
}
