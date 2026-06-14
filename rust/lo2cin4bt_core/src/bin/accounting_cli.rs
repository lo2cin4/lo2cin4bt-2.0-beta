use lo2cin4bt_core::{run_accounting, AccountingInput};
use std::io::{self, Read};

fn main() {
    if let Err(exc) = run() {
        eprintln!("{exc}");
        std::process::exit(2);
    }
}

fn run() -> Result<(), String> {
    let mut input_text = String::new();
    io::stdin()
        .read_to_string(&mut input_text)
        .map_err(|exc| format!("unable to read stdin: {exc}"))?;
    let input: AccountingInput = serde_json::from_str(&input_text)
        .map_err(|exc| format!("invalid accounting json: {exc}"))?;
    let summary = run_accounting(input).map_err(|exc| exc.to_string())?;
    let output = serde_json::to_string_pretty(&summary)
        .map_err(|exc| format!("unable to serialize accounting summary: {exc}"))?;
    println!("{output}");
    Ok(())
}
