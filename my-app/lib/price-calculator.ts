import type { Material, ShopSettings } from "@prisma/client";

export interface PricingInputs {
  materialId: string;
  quantity: number;
  dimensions: string;
  thickness: number;
  laborHours: number;
  finishType: string;
  shopSettings: ShopSettings;
  material: Material;
  marginPercent?: number;
}

export interface PricingBreakdown {
  materialCost: number;
  laborCost: number;
  overheadCost: number;
  subtotal: number;
  marginAmount: number;
  totalPrice: number;
  unitPrice: number;
}

const DENSITY = { steel: 0.283, stainless: 0.29, aluminum: 0.098 } as const;

function parseDimensions(dimensions: string): { width: number; height: number } | null {
  const m = dimensions.toLowerCase().match(/(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)/);
  if (!m) return null;
  return { width: Number(m[1]), height: Number(m[2]) };
}

function estimateMaterialCost(inputs: PricingInputs): number {
  const parsed = parseDimensions(inputs.dimensions);
  if (!parsed) return inputs.material.costPerUnit * inputs.quantity;
  if (inputs.material.unit !== "lb") return inputs.material.costPerUnit * inputs.quantity;

  const vol = parsed.width * parsed.height * Math.max(inputs.thickness || 0.125, 0.01);
  const materialName = inputs.material.name.toLowerCase();
  const density = materialName.includes("aluminum") ? DENSITY.aluminum : materialName.includes("stainless") ? DENSITY.stainless : DENSITY.steel;
  const pounds = vol * density;
  return pounds * inputs.material.costPerUnit * inputs.quantity;
}

const round = (n: number) => Math.round(n * 100) / 100;

export function calculateQuote(inputs: PricingInputs): PricingBreakdown {
  const materialCost = estimateMaterialCost(inputs);
  const laborCost = inputs.laborHours * inputs.shopSettings.laborRatePerHour;
  const overheadCost = (materialCost + laborCost) * (inputs.shopSettings.overheadPercentage / 100);
  const subtotal = materialCost + laborCost + overheadCost;
  const marginPercent = inputs.marginPercent ?? inputs.shopSettings.defaultMarginPercent;
  const marginAmount = subtotal * (marginPercent / 100);
  const totalPrice = subtotal + marginAmount;
  return {
    materialCost: round(materialCost),
    laborCost: round(laborCost),
    overheadCost: round(overheadCost),
    subtotal: round(subtotal),
    marginAmount: round(marginAmount),
    totalPrice: round(totalPrice),
    unitPrice: round(totalPrice / Math.max(inputs.quantity, 1)),
  };
}
