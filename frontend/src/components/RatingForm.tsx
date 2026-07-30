import { Button, Card, Column, Heading, Row, Slider, Text, Textarea } from "@nmmty/dotmatrix";
import { useState } from "react";

import { ratingsApi } from "../api/ratings";

interface RatingFormProps {
  runId: string;
}

function ScoreSlider({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <Column gap="4">
      <Row justifyContent="between">
        <Text fontSize="s">{label}</Text>
        <Text fontSize="s" color="weak">
          {value}/5
        </Text>
      </Row>
      <Slider min={1} max={5} step={1} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </Column>
  );
}

export function RatingForm({ runId }: RatingFormProps) {
  const [thumbs, setThumbs] = useState<boolean | null>(null);
  const [planScore, setPlanScore] = useState(3);
  const [codeScore, setCodeScore] = useState(3);
  const [instructionScore, setInstructionScore] = useState(3);
  const [comment, setComment] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit() {
    setSubmitting(true);
    try {
      await ratingsApi.rate(runId, {
        thumbs,
        plan_score: planScore,
        code_score: codeScore,
        instruction_score: instructionScore,
        comment: comment.trim() || null,
      });
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <Card padding="16" background="surface">
        <Text color="weak">Дякуємо за оцінку!</Text>
      </Card>
    );
  }

  return (
    <Card padding="16" gap="12" background="surface">
      <Heading as="h3" fontSize="s">
        Оцініть цей запуск
      </Heading>
      <Row gap="8">
        <Button
          variant={thumbs === true ? "solid" : "outline"}
          size="s"
          icon="check"
          onClick={() => setThumbs(thumbs === true ? null : true)}
        >
          Сподобалось
        </Button>
        <Button
          variant={thumbs === false ? "solid" : "outline"}
          size="s"
          icon="close"
          onClick={() => setThumbs(thumbs === false ? null : false)}
        >
          Не сподобалось
        </Button>
      </Row>
      <ScoreSlider label="Коректність плану" value={planScore} onChange={setPlanScore} />
      <ScoreSlider label="Якість коду" value={codeScore} onChange={setCodeScore} />
      <ScoreSlider label="Дотримання інструкції" value={instructionScore} onChange={setInstructionScore} />
      <Textarea
        label="Коментар (необовʼязково)"
        rows={2}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
      />
      <Row>
        <Button onClick={onSubmit} disabled={submitting}>
          {submitting ? "Надсилаємо..." : "Надіслати оцінку"}
        </Button>
      </Row>
    </Card>
  );
}
