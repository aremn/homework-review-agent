"use strict";
const path = require("path");
const CopyPlugin = require("copy-webpack-plugin");
const webpack = require("webpack");

module.exports = (env, argv) => {
  const supabaseUrl = process.env.SUPABASE_URL;
  if (!supabaseUrl) {
    throw new Error("Set SUPABASE_URL before building the extension.");
  }
  const backendOrigin = new URL(supabaseUrl).origin;
  const isProduction = argv.mode === "production";
  return {
    mode: isProduction ? "production" : "development",
    entry: {
      background: "./background.js",
      content_script: "./content_script.js",
      popup: "./popup.js",
    },
    output: {
      path: path.resolve(__dirname, "dist"),
      filename: "[name].bundle.js",
      clean: true,
    },
    module: {},
    plugins: [
      new webpack.DefinePlugin({
        "process.env.SUPABASE_URL": JSON.stringify(process.env.SUPABASE_URL || ""),
      }),
      new CopyPlugin({
        patterns: [
          {
            from: "manifest.json",
            to: ".",
            transform(content) {
              const manifest = JSON.parse(content.toString());
              manifest.host_permissions.push(`${backendOrigin}/*`);
              return JSON.stringify(manifest, null, 2);
            },
          },
          { from: "popup.html", to: "." },
          { from: "popup.css", to: "." },
          { from: "styles.css", to: "." },
          { from: "icons", to: "icons" },
        ],
      }),
      new webpack.ProvidePlugin({
        process: "process/browser",
        Buffer: ["buffer", "Buffer"],
      }),
    ],
    resolve: {
      fallback: {
        url: require.resolve("url/"),
        buffer: require.resolve("buffer/"),
        util: require.resolve("util/"),
        stream: require.resolve("stream-browserify"),
        vm: require.resolve("vm-browserify"),
        process: require.resolve("process/browser"),
      },
    },
    optimization: {
      minimize: isProduction,
    },
    devtool: isProduction ? false : "cheap-module-source-map",
  };
};
