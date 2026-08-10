import React, { useMemo } from "react";
import { Chart as ChartJS, defaults } from "chart.js/auto";
import { Line, Bar } from "react-chartjs-2";
import { useApiData } from "../context/ApiContext";
import "../css/charts.css";

defaults.maintainAspectRatio = false;
defaults.responsive = true;
defaults.plugins.title.display = true;
defaults.plugins.title.align = "start";
defaults.plugins.title.font.size = 20;
defaults.plugins.title.color = "black";

// 🔹 Plugin for highlighting area where Act > Std (only for Line charts)
const highlightPlugin = {
  id: "highlightAbove",
  beforeDatasetsDraw(chart, args, opts) {
    if (!chart.data.datasets[1]) return;
    const {
      ctx,
      scales: { x, y },
    } = chart;
    const act = chart.data.datasets[opts.actDataset]?.data;
    const std = chart.data.datasets[opts.stdDataset]?.data;
    if (!act || !std) return;

    ctx.save();
    ctx.fillStyle = opts.color;

    ctx.beginPath();
    for (let i = 0; i < act.length; i++) {
      const xPos = x.getPixelForValue(i);
      const yAct = y.getPixelForValue(act[i]);
      if (i === 0) ctx.moveTo(xPos, yAct);
      else ctx.lineTo(xPos, yAct);
    }

    for (let i = act.length - 1; i >= 0; i--) {
      const xPos = x.getPixelForValue(i);
      const yStd = y.getPixelForValue(std[i]);
      ctx.lineTo(xPos, yStd);
    }

    ctx.closePath();
    ctx.fill();
    ctx.restore();
  },
};

const Charts = () => {
  const { changeoverData, loading, error, selectedRecipe } = useApiData();
  const categoryOrder = ["mechanical", "electrical", "operation", "others"];

  const titleCase = (value) => {
    const text = String(value || "").trim();
    if (!text) return "";
    return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
  };

  const getReasonShadePalette = (category) => {
    const key = String(category || "").trim().toLowerCase();
    if (key === "mechanical") {
      return {
        fill: ["rgba(191, 219, 254, 0.95)", "rgba(96, 165, 250, 0.95)", "rgba(37, 99, 235, 0.95)"],
        border: ["rgba(147, 197, 253, 1)", "rgba(59, 130, 246, 1)", "rgba(30, 64, 175, 1)"],
      };
    }
    if (key === "electrical") {
      return {
        fill: ["rgba(254, 215, 170, 0.95)", "rgba(251, 146, 60, 0.95)", "rgba(194, 65, 12, 0.95)"],
        border: ["rgba(253, 186, 116, 1)", "rgba(249, 115, 22, 1)", "rgba(154, 52, 18, 1)"],
      };
    }
    if (key === "operation") {
      return {
        fill: ["rgba(187, 247, 208, 0.95)", "rgba(74, 222, 128, 0.95)", "rgba(22, 101, 52, 0.95)"],
        border: ["rgba(134, 239, 172, 1)", "rgba(34, 197, 94, 1)", "rgba(21, 128, 61, 1)"],
      };
    }
    return {
      fill: ["rgba(226, 232, 240, 0.95)", "rgba(148, 163, 184, 0.95)", "rgba(71, 85, 105, 0.95)"],
      border: ["rgba(203, 213, 225, 1)", "rgba(100, 116, 139, 1)", "rgba(51, 65, 85, 1)"],
    };
  };

  // Process API data into chart format
  const chartDataList = useMemo(() => {
    if (!changeoverData?.table_data) return [];

    // Filter function for recipe
    const filterByRecipe = (details) => {
      if (!selectedRecipe) return details;
      return details.filter(detail => 
        detail.material && detail.material.includes(selectedRecipe)
      );
    };

    const charts = [];

    // Create line charts for each style type
    changeoverData.table_data.forEach((styleData) => {
      if (styleData.details && styleData.details.length > 0) {
        const filteredDetails = filterByRecipe(styleData.details);
        if (filteredDetails.length > 0) {
          charts.push({
            title: styleData.type,
            type: "line",
            data: filteredDetails.map((detail, index) => ({
              id: index + 1,
              material: detail.material,
              value: detail.act,
              std: detail.Std,
              start_time: detail.start_time,
            })),
          });
        }
      }
    });

    // Add bar chart for overshoot categories
    if (changeoverData?.bar_chart_data && changeoverData.bar_chart_data.length > 0) {
      charts.push({
        title: "Overshoot Categories",
        type: "bar",
        data: changeoverData.bar_chart_data.map((item) => ({
          category: item.category,
          value: item.value,
        })),
      });
    }

    // Add full-width bar chart for reason-wise comparison.
    const allDetails = (changeoverData.table_data || []).flatMap((item) => item.details || []);
    const reasonCountMap = new Map();

    allDetails.forEach((detail) => {
      const category = String(detail.overshoot_category || "").trim();
      const reason = String(detail.overshoot_reason || "").trim();
      if (!category || !reason || category.toLowerCase() === "none" || reason.toLowerCase() === "none") {
        return;
      }

      const normalizedCategory = titleCase(category);
      const normalizedReason = reason;
      const mapKey = `${normalizedCategory}||${normalizedReason}`;
      reasonCountMap.set(mapKey, (reasonCountMap.get(mapKey) || 0) + 1);
    });

    if (reasonCountMap.size > 0) {
      const reasonRows = Array.from(reasonCountMap.entries()).map(([mapKey, value]) => {
        const [category, reason] = mapKey.split("||");
        return { category, reason, value };
      });

      reasonRows.sort((a, b) => {
        const idxA = categoryOrder.indexOf(a.category.toLowerCase());
        const idxB = categoryOrder.indexOf(b.category.toLowerCase());
        const orderA = idxA === -1 ? Number.MAX_SAFE_INTEGER : idxA;
        const orderB = idxB === -1 ? Number.MAX_SAFE_INTEGER : idxB;
        if (orderA !== orderB) return orderA - orderB;
        if (a.category !== b.category) return a.category.localeCompare(b.category);
        if (b.value !== a.value) return b.value - a.value;
        return a.reason.localeCompare(b.reason);
      });

      const categoryCounter = {};
      const backgroundColor = [];
      const borderColor = [];

      reasonRows.forEach((row) => {
        const key = row.category.toLowerCase();
        const shadeIndex = (categoryCounter[key] || 0) % 3;
        categoryCounter[key] = (categoryCounter[key] || 0) + 1;

        const palette = getReasonShadePalette(row.category);
        backgroundColor.push(palette.fill[shadeIndex]);
        borderColor.push(palette.border[shadeIndex]);
      });

      charts.push({
        title: "Overshoot Reasons Comparison",
        type: "reason_bar",
        fullWidth: true,
        data: reasonRows,
        colors: {
          backgroundColor,
          borderColor,
        },
      });
    }

    return charts;
  }, [changeoverData, selectedRecipe]);

  // Loading state
  if (loading && !changeoverData) {
    return (
      <div className="ch-loading-container">
        <div className="ch-spinner"></div>
        <p className="ch-loading-text">Loading charts...</p>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="ch-error-container">
        <svg className="ch-error-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p className="ch-error-text">{error}</p>
      </div>
    );
  }

  // Empty state
  if (chartDataList.length === 0) {
    return (
      <div className="ch-empty-container">
        <svg className="ch-empty-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
        <p className="ch-empty-text">No chart data available for the selected date range.</p>
      </div>
    );
  }

  return (
    <div className="chart-container">
      {chartDataList.map((chartItem, index) => (
        <div className={`chart ${chartItem.fullWidth ? "chart-full-width" : ""}`.trim()} key={index}>
          {chartItem.type === "line" ? (
            <Line
              data={{
                labels: chartItem.data.map((d) => d.id),
                datasets: [
                  {
                    label: "Act time",
                    data: chartItem.data.map((d) => d.value),
                    borderColor: "blue",
                    backgroundColor: "blue",
                    borderWidth: 2,
                    tension: 0,
                    fill: false,
                  },
                  {
                    label: "Std time",
                    data: chartItem.data.map((d) => d.std),
                    borderColor: "yellow",
                    backgroundColor: "yellow",
                    borderWidth: 2,
                    tension: 0,
                    fill: false,
                  },
                ],
              }}
              options={{
                plugins: {
                  legend: {
                    labels: {
                      color: "black",
                    },
                  },
                  title: {
                    display: true,
                    text: chartItem.title,
                  },
                  tooltip: {
                    callbacks: {
                      title: function(context) {
                        const index = context[0].dataIndex;
                        const data = chartItem.data[index];
                        return data.material || `Recipe ${context[0].label}`;
                      },
                      afterTitle: function(context) {
                        const index = context[0].dataIndex;
                        const data = chartItem.data[index];
                        if (data.start_time) {
                          const timestamp = data.start_time.replace('T', ' ').replace('Z', '').substring(0, 19);
                          return `Start Time: ${timestamp}`;
                        }
                        return '';
                      },
                      label: function(context) {
                        const label = context.dataset.label || '';
                        const value = context.parsed.y;
                        return `${label}: ${value.toFixed(2)} min`;
                      },
                    },
                  },
                  highlightAbove: {
                    actDataset: 0,
                    stdDataset: 1,
                    color: "rgba(248, 38, 38, 0.4)",
                  },
                },
                scales: {
                  y: {
                    beginAtZero: false,
                    ticks: {
                      color: "#060606ff",
                      precision: 0,
                      stepSize: 1,
                      callback: function (value) {
                        return Number.isInteger(value) ? value : "";
                      },
                    },
                    grid: { color: "rgba(0, 0, 0, 0.08)" },
                  },
                  x: {
                    ticks: { color: "#040404ff" },
                    grid: { color: "rgba(9, 9, 9, 0.3)" },
                  },
                },
              }}
              plugins={[highlightPlugin]}
            />
          ) : (
            <Bar
              data={{
                labels: chartItem.type === "reason_bar"
                  ? chartItem.data.map((d) => d.reason)
                  : chartItem.data.map((d) => d.category),
                datasets: [
                  {
                    data: chartItem.data.map((d) => d.value),
                    backgroundColor: chartItem.type === "reason_bar"
                      ? chartItem.colors.backgroundColor
                      : [
                          "rgba(54, 162, 235, 0.6)",
                          "rgba(255, 206, 86, 0.6)",
                          "rgba(75, 192, 192, 0.6)",
                          "rgba(255, 99, 132, 0.6)",
                        ],
                    borderColor: chartItem.type === "reason_bar"
                      ? chartItem.colors.borderColor
                      : [
                          "rgba(54, 162, 235, 1)",
                          "rgba(255, 206, 86, 1)",
                          "rgba(75, 192, 192, 1)",
                          "rgba(255, 99, 132, 1)",
                        ],
                    borderWidth: 1,
                  },
                ],
              }}
              options={{
                plugins: {
                  legend: {
                    display: false,
                  },
                  tooltip: {
                    callbacks: {
                      label: function (context) {
                        if (chartItem.type === "reason_bar") {
                          const row = chartItem.data[context.dataIndex];
                          return `${row.category}: ${context.parsed.y}`;
                        }
                        return context.parsed.y;
                      },
                    },
                  },
                  title: {
                    display: true,
                    text: chartItem.title,
                  },
                },
                scales: {
                  y: {
                    beginAtZero: true,
                    ticks: {
                      color: "#060606ff",
                      precision: 0,
                      stepSize: 1,
                      callback: function (value) {
                        return Number.isInteger(value) ? value : "";
                      },
                    },
                    grid: { color: "rgba(0, 0, 0, 0.08)" },
                  },
                  x: {
                    ticks: {
                      color: "#040404ff",
                      maxRotation: chartItem.type === "reason_bar" ? 35 : 0,
                      minRotation: chartItem.type === "reason_bar" ? 20 : 0,
                    },
                    grid: { color: "rgba(9, 9, 9, 0.3)" },
                  },
                },
              }}
            />
          )}
        </div>
      ))}
    </div>
  );
};

export default Charts;
