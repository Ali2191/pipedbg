import { headers } from "next/headers";
import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function POST(req: Request) {
  const signature = headers().get("x-webhook-secret");
  if (!signature || signature !== process.env.EMAIL_WEBHOOK_SECRET) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await req.json();
  const to = String(body.to ?? "");
  const slug = to.split("@")[1]?.split(".")[0];
  if (!slug) return NextResponse.json({ error: "Invalid recipient" }, { status: 400 });
  const shop = await prisma.shop.findUnique({ where: { slug } });
  if (!shop) return NextResponse.json({ error: "Shop not found" }, { status: 404 });

  const quote = await prisma.quote.create({
    data: {
      shopId: shop.id,
      status: "DRAFT",
      extractionStatus: "PENDING",
      sourceEmail: body.from ?? null,
      sourceSubject: body.subject ?? null,
      rawEmailBody: body.text ?? null,
    },
  });

  return NextResponse.json({ ok: true, quoteId: quote.id });
}
