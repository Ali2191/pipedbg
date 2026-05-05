import { PrismaClient, QuoteStatus } from "@prisma/client";
import bcrypt from "bcryptjs";

const prisma = new PrismaClient();

async function main() {
  const passwordHash = await bcrypt.hash("password123", 10);
  const user = await prisma.user.upsert({
    where: { email: "owner@demo-shop.com" },
    update: {},
    create: { email: "owner@demo-shop.com", name: "Demo Owner", passwordHash },
  });

  const shop = await prisma.shop.upsert({
    where: { slug: "demo-fab" },
    update: {},
    create: {
      name: "Demo Fab Shop",
      slug: "demo-fab",
      email: "quote@demo-fab.quotefast.io",
      ownerId: user.id,
      settings: { create: {} },
    },
  });

  const materials = [
    ["304 Stainless Steel", "sheet", 2.9, "lb"],
    ["316 Stainless Steel", "sheet", 3.2, "lb"],
    ["Aluminum 6061", "plate", 2.1, "lb"],
    ["Cold Rolled Steel", "sheet", 1.4, "lb"],
    ["Hot Rolled Steel", "plate", 1.2, "lb"],
  ] as const;

  for (const [name, type, costPerUnit, unit] of materials) {
    await prisma.material.create({ data: { shopId: shop.id, name, type, costPerUnit, unit } });
  }

  await prisma.quote.createMany({
    data: [
      { shopId: shop.id, projectName: "500 Brackets", status: QuoteStatus.DRAFT, customerName: "John" },
      { shopId: shop.id, projectName: "Tube Frame", status: QuoteStatus.SENT, customerName: "Alice", totalPrice: 4200 },
      { shopId: shop.id, projectName: "Panel Cut", status: QuoteStatus.WON, customerName: "Ravi", totalPrice: 2850 },
    ],
  });
}

main().finally(() => prisma.$disconnect());
