import postgresSvg from "../../assets/postgres.svg";
import snowflakeSvg from "../../assets/snowflake.svg";
import sapSvg from "../../assets/sap.svg";
import salesforceSvg from "../../assets/salesforce.svg";
import airflowSvg from "../../assets/airflow.svg";
import awsGlueSvg from "../../assets/aws-glue.svg";
import azureAdfSvg from "../../assets/azure-data-factory.svg";
import netSuiteSvg from "../../assets/netsuite.svg";
import shopifySvg from "../../assets/shopify.svg";
import slackSvg from "../../assets/slack.svg";

/** Logo per source type. Types with no logo of their own fall back to a coloured initial. */
export const SOURCE_ICON = {
  postgres: postgresSvg,
  snowflake: snowflakeSvg,
  sap_s4hana: sapSvg,
  salesforce: salesforceSvg,
  hubspot: salesforceSvg,
  logrocket: postgresSvg,
  bigquery: snowflakeSvg,
  s3: awsGlueSvg,
  airflow: airflowSvg,
  aws_glue: awsGlueSvg,
  mssql: azureAdfSvg,
  dynamics365: azureAdfSvg,
  netsuite: netSuiteSvg,
  shopify: shopifySvg,
  slack: slackSvg,
  sfcc: salesforceSvg,
};

export const BRAND_COLOR = {
  klaviyo: { bg: "#00b8d9", initial: "K" },
  stripe: { bg: "#635bff", initial: "S" },
  magento: { bg: "#ee6723", initial: "M" },
  braze: { bg: "#09c", initial: "B" },
  teams: { bg: "#5059c9", initial: "T" },
  fedex: { bg: "#4d148c", initial: "F" },
  webhook: { bg: "#535862", initial: "W" },
};

/**
 * A source's logo in a fixed-size tile, so a row of them lines up whatever each logo's own
 * aspect ratio is. Shared by the Databases and Connectors menus and by Configure.
 */
export function SourceLogoTile({ sourceType, name = "", src = null }) {
  const logo = src || SOURCE_ICON[sourceType];
  const brand = BRAND_COLOR[sourceType];
  return (
    <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-default">
      {logo ? (
        <img src={logo} alt="" className="h-5 w-5 object-contain" />
      ) : (
        <span
          className="flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold text-white"
          style={{ background: brand?.bg || "#535862" }}
        >
          {brand?.initial || (name.trim()[0] || "?").toUpperCase()}
        </span>
      )}
    </span>
  );
}
