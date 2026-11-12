"use client";
import { useEffect } from "react";

export default function ResearchIndex() {
  useEffect(() => { window.location.replace("/research/datasets"); }, []);
  return <p className="muted">Opening research…</p>;
}
